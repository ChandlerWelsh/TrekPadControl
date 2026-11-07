import Foundation

/// One finished walk, kept on the phone until it's saved to Apple Health.
struct Walk: Codable, Identifiable, Equatable {
    struct Segment: Codable, Equatable {
        var start: Date
        var end: Date
        var distanceM: Int
        var kcal: Int
    }

    var id = UUID()
    var start: Date
    var end: Date
    var durationS: Int
    var distanceM: Int
    var kcal: Int
    var maxInclinePct: Double
    var avgInclinePct: Double
    /// About one per minute of walking, so Health gets a distance/energy timeline.
    var segments: [Segment]
    var savedToHealth = false

    var distanceMiles: Double { Double(distanceM) / FTMS.metersPerMile }
}

/// Turns the pad's running counters into walks. Port of trekpad/workouts.py.
/// A walk starts when the belt moves and ends when it has been stopped for `idleEndSeconds`
/// (a short stop and restart stays one walk). The pad resets its counters on each start, so
/// resets are detected and added up.
@MainActor
final class WalkRecorder: ObservableObject {
    @Published private(set) var active = false
    var onFinish: (@MainActor (Walk) -> Void)?

    let minSeconds: Int
    let idleEndSeconds: Double
    private let clock: () -> Date

    private struct Totals { var el = 0, dist = 0, kcal = 0 }
    private struct Session {
        var start: Date
        var lastMoving: Date
        var offset = Totals()
        var last: Totals
        var lastT: Date
        var maxIncline = 0.0
        var inclineSum = 0.0
        var inclineTime = 0.0
        var segStart: Date
        var segBase = Totals()
        var segments: [Walk.Segment] = []
    }
    private var s: Session?

    init(minSeconds: Int = 60, idleEndSeconds: Double = 90, clock: @escaping () -> Date = { Date() }) {
        self.minSeconds = minSeconds
        self.idleEndSeconds = idleEndSeconds
        self.clock = clock
    }

    func feed(_ d: FTMS.TreadmillData) {
        guard let el = d.elapsedS else { return }
        let now = clock()
        let moving = (d.speedKmh ?? 0) > 0
        let cur = Totals(el: el, dist: d.distanceM ?? 0, kcal: d.kcal ?? 0)
        if s == nil {
            guard moving else { return }
            let start = now.addingTimeInterval(-Double(el))
            s = Session(start: start, lastMoving: now, last: cur, lastT: now, segStart: start)
            active = true
        } else if var ses = s {
            if cur.el + 5 < ses.last.el {      // pad counters reset (stopped, then started again)
                ses.offset.el += ses.last.el
                ses.offset.dist += ses.last.dist
                ses.offset.kcal += ses.last.kcal
            }
            ses.last = cur
            s = ses
        }
        guard var ses = s else { return }
        if moving {
            let dt = now.timeIntervalSince(ses.lastT)
            let inc = d.inclinePct ?? 0
            if dt > 0 && dt < 10 {
                ses.inclineSum += inc * dt
                ses.inclineTime += dt
            }
            ses.maxIncline = max(ses.maxIncline, inc)
            ses.lastMoving = now
            if now.timeIntervalSince(ses.segStart) >= 60 {
                closeSegment(&ses, at: now)
            }
        }
        ses.lastT = now
        s = ses
        tick()
    }

    func tick() {
        if let ses = s, clock().timeIntervalSince(ses.lastMoving) > idleEndSeconds {
            finish()
        }
    }

    @discardableResult
    func finish() -> Walk? {
        guard var ses = s else { return nil }
        s = nil
        active = false
        closeSegment(&ses, at: ses.lastMoving)
        let t = total(ses)
        guard t.el >= minSeconds, t.dist > 0 else { return nil }
        let walk = Walk(start: ses.start, end: ses.lastMoving, durationS: t.el, distanceM: t.dist, kcal: t.kcal,
                        maxInclinePct: ses.maxIncline,
                        avgInclinePct: ses.inclineTime > 0 ? (ses.inclineSum / ses.inclineTime * 10).rounded() / 10 : 0,
                        segments: ses.segments)
        onFinish?(walk)
        return walk
    }

    private func total(_ ses: Session) -> Totals {
        Totals(el: ses.offset.el + ses.last.el, dist: ses.offset.dist + ses.last.dist, kcal: ses.offset.kcal + ses.last.kcal)
    }

    private func closeSegment(_ ses: inout Session, at end: Date) {
        let t = total(ses)
        let dist = t.dist - ses.segBase.dist, kcal = t.kcal - ses.segBase.kcal
        if end > ses.segStart && (dist > 0 || kcal > 0) {
            ses.segments.append(Walk.Segment(start: ses.segStart, end: end, distanceM: max(0, dist), kcal: max(0, kcal)))
        }
        ses.segStart = end
        ses.segBase = t
    }
}
