import XCTest
@testable import TrekPad

/// Same vectors as the Windows app's Python tests (real packets captured from the pad).
final class FTMSTests: XCTestCase {
    private func data(_ hex: String) -> Data {
        var d = Data()
        var i = hex.startIndex
        while i < hex.endIndex {
            let j = hex.index(i, offsetBy: 2)
            d.append(UInt8(hex[i..<j], radix: 16)!)
            i = j
        }
        return d
    }

    func testFullRecordWhileWalking() {
        let r = FTMS.parseTreadmillData(data("8c0501010c00000a0009000100000000001d00"))
        XCTAssertEqual(r.speedKmh, 2.57)
        XCTAssertEqual(r.distanceM, 12)
        XCTAssertEqual(r.inclinePct, 1.0)
        XCTAssertEqual(r.kcal, 1)
        XCTAssertNil(r.heartRate)
        XCTAssertEqual(r.elapsedS, 29)
    }

    func testShortRecordHasSpeedOnly() {
        let r = FTMS.parseTreadmillData(data("1020a10000000000c00800"))
        XCTAssertEqual(r.speedKmh, 1.61)
        XCTAssertNil(r.elapsedS)
    }

    func testTruncatedPacketDoesNotCrash() {
        XCTAssertEqual(FTMS.parseTreadmillData(data("8c05a100")), FTMS.TreadmillData(speedKmh: 1.61))
        XCTAssertEqual(FTMS.parseTreadmillData(Data()), FTMS.TreadmillData())
    }

    func testCommandBytesMatchWhatWorkedLive() {
        XCTAssertEqual(FTMS.start().hex, "07")
        XCTAssertEqual(FTMS.stop().hex, "0801")
        XCTAssertEqual(FTMS.setSpeed(mph: 1.0).hex, "026400")
        XCTAssertEqual(FTMS.setSpeed(mph: 0.6).hex, "023c00")
        XCTAssertEqual(FTMS.setIncline(percent: 1).hex, "030a00")
        XCTAssertEqual(FTMS.setIncline(percent: 0).hex, "030000")
    }

    func testRepliesAndStatus() {
        let r = FTMS.parseControlPointResponse(data("800201"))
        XCTAssertEqual(r?.op, 2)
        XCTAssertEqual(r?.result, 1)
        XCTAssertNil(FTMS.parseControlPointResponse(data("04")))
        XCTAssertEqual(FTMS.parseMachineStatus(data("060a00"))?.inclinePct, 1.0)
        XCTAssertEqual(FTMS.parseMachineStatus(data("05a100"))?.speedKmh, 1.61)
    }
}

final class SpeedJumpTests: XCTestCase {
    func testSingleJumpIsHidden() {
        XCTAssertTrue(PadController.isKnownJump(reported: 2.7, hold: 1.7, holdActive: true, limit: 4))
        XCTAssertTrue(PadController.isKnownJump(reported: 2.4, hold: 1.5, holdActive: true, limit: 4))
    }

    func testCompoundedJumpIsShown() {
        XCTAssertFalse(PadController.isKnownJump(reported: 3.8, hold: 1.5, holdActive: true, limit: 4))
    }

    func testNothingHiddenOutsideTheWindowOrOverTheLimit() {
        XCTAssertFalse(PadController.isKnownJump(reported: 2.7, hold: 1.7, holdActive: false, limit: 4))
        XCTAssertFalse(PadController.isKnownJump(reported: 2.7, hold: 1.7, holdActive: true, limit: 2))
        XCTAssertFalse(PadController.isKnownJump(reported: 1.0, hold: 1.7, holdActive: true, limit: 4))
    }
}

@MainActor
final class WalkRecorderTests: XCTestCase {
    private var now = Date(timeIntervalSince1970: 1_794_000_000)
    private var finished: [Walk] = []

    private func makeRecorder() -> WalkRecorder {
        let r = WalkRecorder(minSeconds: 60, idleEndSeconds: 90, clock: { [unowned self] in self.now })
        r.onFinish = { [unowned self] w in self.finished.append(w) }
        return r
    }

    private func walk(_ r: WalkRecorder, seconds: Int, startEl: Int = 0, speedKmh: Double = 3.2, incline: Double = 0) {
        for i in 0...seconds {
            r.feed(FTMS.TreadmillData(speedKmh: speedKmh, distanceM: Int(Double(i) * speedKmh / 3.6),
                                      inclinePct: incline, kcal: i / 20, elapsedS: startEl + i))
            now += 1
        }
    }

    private func idle(_ r: WalkRecorder, seconds: Int, el: Int, dist: Int) {
        for _ in 0..<seconds {
            r.feed(FTMS.TreadmillData(speedKmh: 0, distanceM: dist, inclinePct: 0, kcal: 0, elapsedS: el))
            now += 1
        }
    }

    func testWalkThenIdleSavesOneWalkWithSegments() {
        let r = makeRecorder()
        walk(r, seconds: 600, incline: 2)
        XCTAssertTrue(r.active)
        idle(r, seconds: 100, el: 600, dist: 533)
        XCTAssertFalse(r.active)
        XCTAssertEqual(finished.count, 1)
        let w = finished[0]
        XCTAssertEqual(w.durationS, 600)
        XCTAssertEqual(w.distanceM, 533)
        XCTAssertEqual(w.maxInclinePct, 2)
        XCTAssertEqual(w.avgInclinePct, 2)
        XCTAssertGreaterThanOrEqual(w.segments.count, 9)          // about one per minute
        XCTAssertEqual(w.segments.reduce(0) { $0 + $1.distanceM }, 533)
    }

    func testStopAndRestartIsOneWalk() {
        let r = makeRecorder()
        walk(r, seconds: 300)
        idle(r, seconds: 30, el: 300, dist: 266)
        walk(r, seconds: 300)                                      // pad counters restart from zero
        r.finish()
        XCTAssertEqual(finished.count, 1)
        XCTAssertEqual(finished[0].durationS, 600)
        XCTAssertEqual(finished[0].distanceM, 532)
    }

    func testShortTestRunsAreNotSaved() {
        let r = makeRecorder()
        walk(r, seconds: 40)
        r.finish()
        XCTAssertTrue(finished.isEmpty)
    }
}
