import CoreBluetooth
import Foundation

/// Bluetooth Fitness Machine Service (FTMS) helpers for the NovaWalk W50 / MRK-T25.
/// Same protocol and quirks as the Windows app (trekpad/ftms.py, research/FINDINGS.md):
/// - SET_SPEED targets are read by the pad as 0.01 mph (the spec says 0.01 km/h); Treadmill
///   Data reports real 0.01 km/h.
/// - Every SET_INCLINE makes the pad re-target speed to (current km/h number as mph), a x1.6
///   jump. Wait for the incline to be confirmed, then resend speed. Jumps compound if a new
///   incline is sent before the previous jump is corrected.
enum FTMS {
    static let service = CBUUID(string: "1826")
    static let controlPoint = CBUUID(string: "2AD9")
    static let treadmillData = CBUUID(string: "2ACD")
    static let machineStatus = CBUUID(string: "2ADA")

    static let kmhPerMph = 1.609344
    static let metersPerMile = 1609.344
    static let minMph = 0.6
    static let maxMph = 4.0
    static let minIncline = 0
    static let maxIncline = 12

    static let statusStopped: UInt8 = 0x02
    static let statusSafetyKey: UInt8 = 0x03
    static let statusStarted: UInt8 = 0x04
    static let statusTargetSpeed: UInt8 = 0x05
    static let statusTargetIncline: UInt8 = 0x06
    static let statusControlLost: UInt8 = 0xFF

    static let results: [UInt8: String] = [1: "success", 2: "not supported", 3: "invalid parameter",
                                           4: "operation failed", 5: "control not permitted"]

    // MARK: commands

    static func requestControl() -> Data { Data([0x00]) }
    static func start() -> Data { Data([0x07]) }
    static func stop() -> Data { Data([0x08, 0x01]) }

    /// Target speed. This pad reads the value as 0.01 mph.
    static func setSpeed(mph: Double) -> Data {
        let v = UInt16(clamping: Int((mph * 100).rounded()))
        return Data([0x02, UInt8(v & 0xFF), UInt8(v >> 8)])
    }

    static func setIncline(percent: Double) -> Data {
        let v = UInt16(bitPattern: Int16(clamping: Int((percent * 10).rounded())))
        return Data([0x03, UInt8(v & 0xFF), UInt8(v >> 8)])
    }

    // MARK: parsing

    struct TreadmillData: Equatable {
        var speedKmh: Double?
        var distanceM: Int?
        var inclinePct: Double?
        var kcal: Int?
        var heartRate: Int?
        var elapsedS: Int?
    }

    struct MachineStatus: Equatable {
        var code: UInt8
        var speedKmh: Double?
        var inclinePct: Double?
    }

    /// Control point indication -> (request opcode, result code).
    static func parseControlPointResponse(_ data: Data) -> (op: UInt8, result: UInt8)? {
        let b = [UInt8](data)
        guard b.count >= 3, b[0] == 0x80 else { return nil }
        return (b[1], b[2])
    }

    static func parseMachineStatus(_ data: Data) -> MachineStatus? {
        let b = [UInt8](data)
        guard let code = b.first else { return nil }
        var s = MachineStatus(code: code)
        if b.count >= 3 {
            let raw = UInt16(b[1]) | UInt16(b[2]) << 8
            if code == statusTargetSpeed { s.speedKmh = Double(raw) / 100 }
            if code == statusTargetIncline { s.inclinePct = Double(Int16(bitPattern: raw)) / 10 }
        }
        return s
    }

    /// Treadmill Data (0x2ACD). The pad alternates a full record (flags 0x058C: speed,
    /// distance, incline, energy, heart rate, elapsed time) and a short one (flags 0x2010).
    static func parseTreadmillData(_ data: Data) -> TreadmillData {
        var out = TreadmillData()
        let b = [UInt8](data)
        guard b.count >= 2 else { return out }
        let flags = UInt16(b[0]) | UInt16(b[1]) << 8
        var i = 2

        func take(_ n: Int) -> [UInt8]? {
            guard i + n <= b.count else { return nil }
            defer { i += n }
            return Array(b[i..<(i + n)])
        }
        func u16() -> Int? { take(2).map { Int($0[0]) | Int($0[1]) << 8 } }
        func s16() -> Int? { take(2).map { Int(Int16(bitPattern: UInt16($0[0]) | UInt16($0[1]) << 8)) } }

        if flags & 0x0001 == 0 { guard let v = u16() else { return out }; out.speedKmh = Double(v) / 100 }
        if flags & 0x0002 != 0 { guard take(2) != nil else { return out } }          // average speed
        if flags & 0x0004 != 0 {
            guard let t = take(3) else { return out }
            out.distanceM = Int(t[0]) | Int(t[1]) << 8 | Int(t[2]) << 16
        }
        if flags & 0x0008 != 0 {                                                    // incline + ramp angle
            guard let v = s16(), take(2) != nil else { return out }
            out.inclinePct = Double(v) / 10
        }
        if flags & 0x0010 != 0 { guard take(4) != nil else { return out } }          // elevation gain
        if flags & 0x0020 != 0 { guard take(1) != nil else { return out } }          // instantaneous pace
        if flags & 0x0040 != 0 { guard take(1) != nil else { return out } }          // average pace
        if flags & 0x0080 != 0 {                                                    // energy
            guard let total = u16(), take(3) != nil else { return out }
            if total != 0xFFFF { out.kcal = total }
        }
        if flags & 0x0100 != 0 {                                                    // heart rate
            guard let hr = take(1) else { return out }
            out.heartRate = hr[0] == 0 ? nil : Int(hr[0])
        }
        if flags & 0x0200 != 0 { guard take(1) != nil else { return out } }          // MET
        if flags & 0x0400 != 0 { guard let v = u16() else { return out }; out.elapsedS = v }
        return out
    }
}

extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}
