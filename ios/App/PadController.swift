import CoreBluetooth
import Foundation
import UIKit

struct PadError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct Toast: Equatable, Identifiable {
    let id = UUID()
    let text: String
}

/// Serializes async work on the main actor (one control-point command, or one user action, at a time).
@MainActor
final class Gate {
    private var busy = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func run<T>(_ operation: () async throws -> T) async rethrows -> T {
        if busy {
            await withCheckedContinuation { waiters.append($0) }
        } else {
            busy = true
        }
        defer {
            if waiters.isEmpty { busy = false } else { waiters.removeFirst().resume() }
        }
        return try await operation()
    }
}

/// Safety tuning, same values as the Windows app.
enum Tuning {
    static let speedTolerance = 0.15          // how far over target counts as "jumped"
    static let guardSeconds = 5.0             // watch for the speed jump this long after an incline change
    static let startGrace = 10.0              // belt counts down ~3 s after START before it moves
    static let maxCorrections = 3             // failed speed corrections before STOP
    static let staleSeconds = 5.0             // pad sends data ~every 0.8 s; silence = link is dead
}

/// Bluetooth connection, commands and safety logic for the pad. Port of trekpad/pad.py.
@MainActor
final class PadController: NSObject, ObservableObject {

    @Published private(set) var connected = false
    @Published private(set) var status = "Starting…"
    @Published private(set) var deviceName: String?
    @Published private(set) var reportedSpeedMph = 0.0
    @Published private(set) var inclinePct = 0.0
    @Published private(set) var distanceM = 0
    @Published private(set) var kcal = 0
    @Published private(set) var elapsedS = 0
    @Published private(set) var startRequested = Date.distantPast
    @Published var toast: Toast?
    @Published var maxMph: Double {
        didSet { UserDefaults.standard.set(maxMph, forKey: "maxMph") }
    }

    /// Called with each full Treadmill Data record (has elapsed time); feeds the walk recorder.
    var onSample: (@MainActor (FTMS.TreadmillData) -> Void)?

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var controlPoint: CBCharacteristic?
    private let cpGate = Gate()
    private let actionGate = Gate()
    private var cpPending: (id: Int, cont: CheckedContinuation<(op: UInt8, result: UInt8)?, Never>)?
    private var cpSeq = 0

    private var desiredMph: Double?
    private var lastSpeedCommand = Date.distantPast
    private var lastStartedStatus = Date.distantPast
    private var statusIncline: Double?
    private var guardUntil = Date.distantPast
    private var guardMph: Double?
    private var guardCorrections = 0
    private var capCorrections = 0
    private var lastCorrection = Date.distantPast
    private var lastData = Date.distantPast
    private var holdMph: Double?
    private var holdUntil = Date.distantPast
    private var watchdog: Timer?
    private var checking = false

    private var knownPeripheralID: UUID? {
        get { UserDefaults.standard.string(forKey: "padID").flatMap(UUID.init(uuidString:)) }
        set { UserDefaults.standard.set(newValue?.uuidString, forKey: "padID") }
    }

    override init() {
        let saved = UserDefaults.standard.double(forKey: "maxMph")
        maxMph = saved > 0 ? saved : FTMS.maxMph
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
        watchdog = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
    }

    // MARK: derived state

    var isRunning: Bool {
        reportedSpeedMph > 0.05 || Date().timeIntervalSince(startRequested) < Tuning.startGrace
    }

    var speedLimit: Double { max(FTMS.minMph, min(maxMph, FTMS.maxMph)) }

    /// What the gauge shows: hides only the pad's single x1.6 jump right after one of our incline
    /// changes. Anything else (a compounded jump, the remote, going over the max) is shown as-is.
    var displayedSpeedMph: Double {
        Self.isKnownJump(reported: reportedSpeedMph, hold: holdMph,
                         holdActive: Date() < holdUntil, limit: speedLimit) ? (holdMph ?? reportedSpeedMph) : reportedSpeedMph
    }

    nonisolated static func isKnownJump(reported: Double, hold: Double?, holdActive: Bool, limit: Double) -> Bool {
        guard let hold, holdActive, reported <= limit + Tuning.speedTolerance else { return false }
        let jumped = (hold * FTMS.kmhPerMph * 10).rounded() / 10   // what the firmware re-targets to
        return abs(reported - jumped) <= 0.15
    }

    // MARK: commands

    func start() async throws {
        try await cp(FTMS.start(), "start")
        startRequested = Date()
        desiredMph = nil
        let t0 = startRequested
        Task {
            try? await Task.sleep(for: .seconds(8))
            if self.lastStartedStatus < t0, self.reportedSpeedMph <= 0.05, self.startRequested == t0 {
                self.startRequested = .distantPast
                self.say("The pad didn't respond to Start. Check that it's on and nearby.")
            }
        }
    }

    /// No action gate: STOP must never wait behind another command.
    func stop() async throws {
        guardUntil = .distantPast
        holdUntil = .distantPast
        desiredMph = nil
        startRequested = .distantPast
        try await cp(FTMS.stop(), "stop")
    }

    func setSpeed(_ mph: Double) async throws {
        try await actionGate.run { try await self.applySpeed(mph) }
    }

    func changeSpeed(by delta: Double) async throws {
        try await actionGate.run { try await self.applySpeed(self.baseSpeed() + delta) }
    }

    func setIncline(_ pct: Double) async throws {
        try await actionGate.run { try await self.applyIncline(pct) }
    }

    func changeIncline(by delta: Double) async throws {
        try await actionGate.run { try await self.applyIncline(self.inclinePct + delta) }
    }

    /// Fire-and-forget wrapper for buttons; errors become a toast.
    func perform(_ action: @escaping (PadController) async throws -> Void) {
        Task {
            do { try await action(self) } catch { self.say(error.localizedDescription) }
        }
    }

    // MARK: internals

    private func baseSpeed() -> Double {
        if let d = desiredMph, Date().timeIntervalSince(lastSpeedCommand) < 3 { return d }
        return (reportedSpeedMph * 10).rounded() / 10
    }

    private func waitUntilMoving() async throws {
        if reportedSpeedMph > 0.05 { return }
        if Date().timeIntervalSince(startRequested) > Tuning.startGrace {
            throw PadError("The belt isn't running. Press Start first.")
        }
        let deadline = Date().addingTimeInterval(Tuning.startGrace)
        while reportedSpeedMph <= 0.05 {
            if Date() > deadline { throw PadError("The belt didn't start moving.") }
            try await Task.sleep(for: .milliseconds(200))
        }
    }

    @discardableResult
    private func sendSpeed(_ mph: Double, _ label: String) async throws -> Double {
        let v = ((min(max(mph, FTMS.minMph), speedLimit)) * 10).rounded() / 10
        desiredMph = v
        lastSpeedCommand = Date()
        try await cp(FTMS.setSpeed(mph: v), "\(label) \(v) mph")
        return v
    }

    private func applySpeed(_ mph: Double) async throws {
        try await waitUntilMoving()
        let v = try await sendSpeed(mph, "set speed")
        if Date() < guardUntil { guardMph = v }
        if Date() < holdUntil { holdMph = v }
    }

    private func applyIncline(_ pct: Double) async throws {
        try await waitUntilMoving()
        let target = Int(min(max(pct, Double(FTMS.minIncline)), Double(FTMS.maxIncline)).rounded())
        if abs(inclinePct - Double(target)) < 0.05 { return }
        let hold = max(baseSpeed(), FTMS.minMph)
        // Close any earlier guard window: a correction while this incline is pending would cancel it.
        guardUntil = .distantPast
        // Settle an uncorrected earlier jump first, or the jumps compound (1.5 -> 2.4 -> 3.8 mph).
        if reportedSpeedMph > hold + Tuning.speedTolerance {
            try await sendSpeed(hold, "settle speed")
            let t = Date()
            while reportedSpeedMph > hold + Tuning.speedTolerance {
                if Date().timeIntervalSince(t) > 3 {
                    throw PadError("The speed didn't settle, so the incline wasn't changed. Try again.")
                }
                try await Task.sleep(for: .milliseconds(50))
            }
        }
        statusIncline = nil
        let t0 = Date()
        holdMph = hold
        holdUntil = t0.addingTimeInterval(3 + Tuning.guardSeconds)
        try await cp(FTMS.setIncline(percent: Double(target)), "set incline \(target)%")
        // Wait for the pad to accept the incline before correcting speed; correcting earlier
        // cancels the incline change.
        while Date().timeIntervalSince(t0) < 3 {
            if statusIncline == Double(target) || abs(inclinePct - Double(target)) < 0.05 { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        try await sendSpeed(hold, "hold speed")
        guardMph = hold
        guardCorrections = 0
        lastCorrection = Date()
        guardUntil = Date().addingTimeInterval(Tuning.guardSeconds)
        holdUntil = guardUntil.addingTimeInterval(0.5)
    }

    private func emergencyStop(_ reason: String) async {
        log("emergency stop: \(reason)")
        try? await stop()
        say("Belt stopped for safety: \(reason)")
    }

    private func tick() {
        guard connected, !checking else { return }
        if Date().timeIntervalSince(lastData) > Tuning.staleSeconds {
            dropStaleLink()
            return
        }
        checking = true
        Task {
            defer { self.checking = false }
            await self.checkSpeed()
        }
    }

    private func checkSpeed() async {
        let now = Date()
        guard now.timeIntervalSince(lastCorrection) >= 1 else { return }
        let mph = reportedSpeedMph
        let limit = speedLimit
        if mph > limit + Tuning.speedTolerance {
            capCorrections += 1
            lastCorrection = now
            if capCorrections > Tuning.maxCorrections {
                await emergencyStop("speed stayed above your \(String(format: "%.1f", limit)) mph limit")
            } else {
                _ = try? await sendSpeed(limit, "limit speed")
                if capCorrections == 1 { say("Speed pulled back to your \(String(format: "%.1f", limit)) mph limit") }
            }
            return
        }
        capCorrections = 0
        if now < guardUntil, let g = guardMph, mph > g + Tuning.speedTolerance {
            guardCorrections += 1
            lastCorrection = now
            if guardCorrections > Tuning.maxCorrections {
                await emergencyStop("speed jumped after an incline change and wouldn't settle")
            } else {
                _ = try? await sendSpeed(g, "correct speed")
            }
        }
    }

    private func cp(_ data: Data, _ label: String, timeout: Double = 5) async throws {
        guard let p = peripheral, let c = controlPoint, connected,
              Date().timeIntervalSince(lastData) < Tuning.staleSeconds else {
            throw PadError("The pad isn't connected. Check that it's on and nearby.")
        }
        let reply: (op: UInt8, result: UInt8)? = await cpGate.run {
            log("send \(label) (\(data.hex))")
            return await withCheckedContinuation { cont in
                self.cpSeq += 1
                let id = self.cpSeq
                self.cpPending = (id, cont)
                p.writeValue(data, for: c, type: .withResponse)
                Task {
                    try? await Task.sleep(for: .seconds(timeout))
                    if let pending = self.cpPending, pending.id == id {
                        self.cpPending = nil
                        pending.cont.resume(returning: nil)   // the pad sometimes doesn't answer; command still applies
                    }
                }
            }
        }
        if let reply, reply.result != 1 {
            throw PadError("The pad refused \(label): \(FTMS.results[reply.result] ?? "code \(reply.result)")")
        }
    }

    // MARK: connection

    private func scan() {
        guard central.state == .poweredOn else { return }
        if let id = knownPeripheralID, let p = central.retrievePeripherals(withIdentifiers: [id]).first {
            connect(p)
            return
        }
        status = "Searching for the pad…"
        central.scanForPeripherals(withServices: [FTMS.service])
    }

    private func connect(_ p: CBPeripheral) {
        central.stopScan()
        peripheral = p
        p.delegate = self
        status = "Connecting to \(p.name ?? "the pad")…"
        // iOS connection requests don't time out: this also reconnects when the pad comes back.
        central.connect(p)
    }

    private func dropStaleLink() {
        log("no data for \(Tuning.staleSeconds) s; treating the link as lost")
        connected = false
        status = "Lost the pad's signal. Reconnecting…"
        say("Lost the pad's signal. Reconnecting…")
        if let p = peripheral { central.cancelPeripheralConnection(p) }
    }

    private func didGetCharacteristics(_ chars: [CBCharacteristic], on p: CBPeripheral) {
        for c in chars {
            if c.uuid == FTMS.controlPoint { controlPoint = c }
            if [FTMS.controlPoint, FTMS.treadmillData, FTMS.machineStatus].contains(c.uuid) {
                p.setNotifyValue(true, for: c)
            }
        }
        guard controlPoint != nil else { status = "This device has no FTMS control point"; return }
        knownPeripheralID = p.identifier
        deviceName = p.name
        lastData = Date()   // grace period until the first data arrives
        connected = true
        status = "Connected to \(p.name ?? "the pad")"
        log("connected to \(p.name ?? "?")")
        Task {
            try? await Task.sleep(for: .milliseconds(600))
            try? await self.cp(FTMS.requestControl(), "request control", timeout: 2)
        }
    }

    private func handle(_ data: Data, from uuid: CBUUID) {
        switch uuid {
        case FTMS.controlPoint:
            if let r = FTMS.parseControlPointResponse(data), let pending = cpPending {
                cpPending = nil
                pending.cont.resume(returning: r)
            }
        case FTMS.machineStatus:
            guard let st = FTMS.parseMachineStatus(data) else { return }
            switch st.code {
            case FTMS.statusStarted: lastStartedStatus = Date()
            case FTMS.statusTargetIncline: statusIncline = st.inclinePct
            case FTMS.statusStopped, FTMS.statusSafetyKey:
                // "safety key" (03) also arrives while idle right after connecting.
                if st.code == FTMS.statusSafetyKey, reportedSpeedMph > 0.05 { say("The pad reported a safety stop") }
                startRequested = .distantPast
            case FTMS.statusControlLost: say("The pad says another device took control")
            default: break
            }
        case FTMS.treadmillData:
            let rec = FTMS.parseTreadmillData(data)
            lastData = Date()
            if let s = rec.speedKmh { reportedSpeedMph = s / FTMS.kmhPerMph }
            if let v = rec.inclinePct { inclinePct = v }
            if let v = rec.distanceM { distanceM = v }
            if let v = rec.kcal { kcal = v }
            if let v = rec.elapsedS { elapsedS = v }
            if rec.elapsedS != nil { onSample?(rec) }
            UIApplication.shared.isIdleTimerDisabled = isRunning   // keep the screen on while walking
        default:
            break
        }
    }

    func say(_ text: String) {
        log(text)
        toast = Toast(text: text)
    }

    private func log(_ text: String) {
        print("[TrekPad] \(text)")
    }
}

// MARK: - CoreBluetooth delegates (callbacks arrive on the main queue)

extension PadController: CBCentralManagerDelegate, CBPeripheralDelegate {
    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
        MainActor.assumeIsolated {
            switch central.state {
            case .poweredOn: scan()
            case .unauthorized: status = "Allow Bluetooth for TrekPad in Settings"
            case .poweredOff: status = "Bluetooth is off"; connected = false
            default: status = "Bluetooth isn't available"
            }
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                                    advertisementData: [String: Any], rssi RSSI: NSNumber) {
        MainActor.assumeIsolated {
            let name = (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? peripheral.name ?? ""
            if name.hasPrefix("MRK-") { connect(peripheral) }
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        MainActor.assumeIsolated {
            peripheral.discoverServices([FTMS.service])
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated {
            status = "Can't reach the pad, retrying…"
            central.connect(peripheral)
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated {
            connected = false
            controlPoint = nil
            status = "Lost the pad's signal. Reconnecting…"
            if let pending = cpPending { cpPending = nil; pending.cont.resume(returning: nil) }
            central.connect(peripheral)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        MainActor.assumeIsolated {
            guard let s = peripheral.services?.first(where: { $0.uuid == FTMS.service }) else {
                status = "The pad doesn't offer FTMS"
                return
            }
            peripheral.discoverCharacteristics([FTMS.controlPoint, FTMS.treadmillData, FTMS.machineStatus], for: s)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        MainActor.assumeIsolated {
            didGetCharacteristics(service.characteristics ?? [], on: peripheral)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        MainActor.assumeIsolated {
            if let data = characteristic.value { handle(data, from: characteristic.uuid) }
        }
    }
}
