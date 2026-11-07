import SwiftUI
import UIKit

// MARK: - Palette (same tokens as the Windows app's style.css)

struct Palette {
    let bg, card, line, text, muted, accent, accentSoft, accentText, onAccent, stop, onStop, track, over, warn: Color

    static let dark = Palette(
        bg: Color(hex: 0x121417), card: Color(hex: 0x1C1F24), line: Color(hex: 0x2A2E35),
        text: Color(hex: 0xE8EAED), muted: Color(hex: 0x8B919A), accent: Color(hex: 0xC8F25A),
        accentSoft: Color(hex: 0xC8F25A, alpha: 0.32), accentText: Color(hex: 0xC8F25A), onAccent: Color(hex: 0x172000),
        stop: Color(hex: 0xFF5A5F), onStop: Color(hex: 0x1A0606), track: Color(hex: 0x2A2E35),
        over: Color(hex: 0x3A2427), warn: Color(hex: 0xFFB547))

    static let light = Palette(
        bg: Color(hex: 0xF3F4F6), card: .white, line: Color(hex: 0xE1E4E9),
        text: Color(hex: 0x15171A), muted: Color(hex: 0x6B717B), accent: Color(hex: 0x7FB800),
        accentSoft: Color(hex: 0x7FB800, alpha: 0.28), accentText: Color(hex: 0x4B7000), onAccent: .white,
        stop: Color(hex: 0xD93036), onStop: .white, track: Color(hex: 0xE4E7EC),
        over: Color(hex: 0xF5DFE0), warn: Color(hex: 0xB76E00))
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255, opacity: alpha)
    }
}

private func haptic() { UIImpactFeedbackGenerator(style: .medium).impactOccurred() }

private func fmtTime(_ s: Int) -> String {
    let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
    return h > 0 ? String(format: "%ld:%02ld:%02ld", h, m, sec) : String(format: "%ld:%02ld", m, sec)
}

// MARK: - Main screen

struct ContentView: View {
    @EnvironmentObject var pad: PadController
    @EnvironmentObject var recorder: WalkRecorder
    @EnvironmentObject var health: HealthSync
    @Environment(\.colorScheme) private var scheme
    @State private var showSettings = false

    private var p: Palette { scheme == .dark ? .dark : .light }

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                header
                SpeedGauge(palette: p)
                InclineCard(palette: p)
                stats
                actions
                footer
            }
            .padding(.horizontal, 18)
            .padding(.bottom, 24)
        }
        .scrollBounceBehavior(.basedOnSize)
        .background(p.bg.ignoresSafeArea())
        .foregroundStyle(p.text)
        .overlay(alignment: .bottom) { toast }
        .sheet(isPresented: $showSettings) { SettingsView() }
    }

    private var header: some View {
        let (text, color): (String, Color) = {
            if !pad.connected { return ("Not connected · \(pad.status.lowercasedFirst)", p.warn) }
            if pad.isRunning { return (pad.reportedSpeedMph > 0.05 ? "Walking" : "Starting…", p.accentText) }
            return ("Connected · ready", p.muted)
        }()
        return HStack {
            Circle().fill(pad.connected ? (pad.isRunning ? p.accent : p.accentText.opacity(0.7)) : p.warn)
                .frame(width: 8, height: 8)
            Text(text).font(.subheadline).foregroundStyle(color).lineLimit(1)
            Spacer()
            Button { showSettings = true } label: {
                Image(systemName: "gearshape").font(.title3).foregroundStyle(p.muted)
            }
            .accessibilityLabel("Settings")
        }
        .padding(.top, 8)
    }

    private var stats: some View {
        HStack(spacing: 8) {
            StatTile(value: pad.connected ? fmtTime(pad.elapsedS) : "–", label: "Time", palette: p)
            StatTile(value: pad.connected ? String(format: "%.2f mi", Double(pad.distanceM) / FTMS.metersPerMile) : "–",
                     label: "Distance", palette: p)
            StatTile(value: pad.connected ? "\(pad.kcal)" : "–", label: "Calories", palette: p)
        }
        .opacity(pad.connected ? 1 : 0.45)
    }

    private var actions: some View {
        HStack(spacing: 10) {
            Button {
                haptic()
                pad.perform { try await $0.start() }
            } label: {
                Label("Start", systemImage: "play.fill").frame(maxWidth: .infinity, minHeight: 54)
            }
            .buttonStyle(.plain)
            .background(p.card, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(p.line))
            .opacity(pad.connected ? 1 : 0.45)
            .frame(maxWidth: 130)

            Button {
                haptic()
                pad.perform { try await $0.stop() }
            } label: {
                Label("Stop", systemImage: "stop.fill").font(.title3.weight(.semibold))
                    .frame(maxWidth: .infinity, minHeight: 54)
            }
            .buttonStyle(.plain)
            .foregroundStyle(p.onStop)
            .background(p.stop, in: RoundedRectangle(cornerRadius: 12))
        }
    }

    private var footer: some View {
        HStack {
            Image(systemName: "heart").foregroundStyle(p.stop)
            Text(healthText).font(.footnote).foregroundStyle(p.muted)
            Spacer()
            if recorder.active {
                HStack(spacing: 6) {
                    Circle().fill(p.stop).frame(width: 6, height: 6)
                    Text("Recording").font(.footnote).foregroundStyle(p.muted)
                }
            }
        }
    }

    private var healthText: String {
        if !health.available { return "Apple Health isn't available" }
        let n = health.waitingCount
        if n > 0 { return "\(n) walk\(n == 1 ? "" : "s") waiting for Health" }
        return health.walks.isEmpty ? "Walks save to Apple Health" : "Apple Health up to date"
    }

    @ViewBuilder private var toast: some View {
        if let t = pad.toast {
            Text(t.text)
                .font(.subheadline)
                .padding(.horizontal, 14).padding(.vertical, 10)
                .background(p.text, in: RoundedRectangle(cornerRadius: 10))
                .foregroundStyle(p.bg)
                .padding(.horizontal, 18).padding(.bottom, 12)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .task(id: t.id) {
                    try? await Task.sleep(for: .seconds(3.2))
                    withAnimation { if pad.toast?.id == t.id { pad.toast = nil } }
                }
        }
    }
}

extension String {
    var lowercasedFirst: String { prefix(1).lowercased() + dropFirst() }
}

struct StatTile: View {
    let value: String, label: String, palette: Palette
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title3.weight(.semibold)).monospacedDigit()
            Text(label).font(.caption).foregroundStyle(palette.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(palette.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(palette.line))
    }
}

// MARK: - Speed gauge: tap or drag anywhere on the ring to set the speed

struct GaugeArc: Shape {
    var from: Double   // degrees; 0 = right, increasing clockwise on screen
    var to: Double
    func path(in rect: CGRect) -> Path {
        var path = Path()
        guard to - from > 0.5 else { return path }
        path.addArc(center: CGPoint(x: rect.midX, y: rect.midY), radius: min(rect.width, rect.height) / 2,
                    startAngle: .degrees(from), endAngle: .degrees(to), clockwise: false)
        return path
    }
}

struct SpeedGauge: View {
    @EnvironmentObject var pad: PadController
    let palette: Palette
    @State private var preview: Double?
    @State private var dragging = false

    static let a0 = 135.0, span = 270.0
    static func angle(_ mph: Double) -> Double {
        a0 + (min(max(mph, FTMS.minMph), FTMS.maxMph) - FTMS.minMph) / (FTMS.maxMph - FTMS.minMph) * span
    }

    var body: some View {
        let speed = pad.connected ? pad.displayedSpeedMph : 0
        GeometryReader { geo in
            let size = min(geo.size.width, 300.0)
            let ring = size - 70                       // leaves room for the labels outside
            let r = ring / 2
            let center = CGPoint(x: geo.size.width / 2, y: size / 2)
            ZStack {
                Group {
                    GaugeArc(from: Self.a0, to: Self.a0 + Self.span).stroke(palette.track, style: .init(lineWidth: 16, lineCap: .round))
                    if pad.speedLimit < FTMS.maxMph {
                        GaugeArc(from: Self.angle(pad.speedLimit), to: Self.a0 + Self.span)
                            .stroke(palette.over, style: .init(lineWidth: 16, lineCap: .round))
                    }
                    if speed >= FTMS.minMph - 0.05 {
                        GaugeArc(from: Self.a0, to: Self.angle(speed))
                            .stroke(palette.accent, style: .init(lineWidth: 16, lineCap: .round))
                            .animation(.easeOut(duration: 0.25), value: speed)
                    }
                }
                .frame(width: ring, height: ring)
                .position(center)

                ForEach(Array(stride(from: 1.0, through: 4.0, by: 0.5)), id: \.self) { v in
                    let major = v == v.rounded()
                    let a = Self.angle(v) * .pi / 180
                    Capsule().fill(palette.muted.opacity(major ? 1 : 0.55))
                        .frame(width: major ? 2 : 1.5, height: major ? 9 : 5)
                        .rotationEffect(.radians(a + .pi / 2))
                        .position(x: center.x + (r - 20) * cos(a), y: center.y + (r - 20) * sin(a))
                    if major {
                        Text("\(Int(v))").font(.caption2).foregroundStyle(palette.muted)
                            .position(x: center.x + (r + 22) * cos(a), y: center.y + (r + 22) * sin(a))
                    }
                }

                if let k = preview {
                    let a = Self.angle(k) * .pi / 180
                    Circle().fill(palette.bg).overlay(Circle().stroke(palette.accent, lineWidth: 4))
                        .frame(width: 22, height: 22)
                        .position(x: center.x + r * cos(a), y: center.y + r * sin(a))
                }

                VStack(spacing: 2) {
                    Text(preview.map { String(format: "%.1f", $0) } ?? (pad.connected ? String(format: "%.1f", speed) : "–"))
                        .font(.system(size: 64, weight: .semibold, design: .rounded)).monospacedDigit()
                        .foregroundStyle(preview != nil ? palette.accentText : palette.text)
                    Text("mph").font(.subheadline).foregroundStyle(palette.muted)
                    Text(hint).font(.caption).foregroundStyle(palette.muted).frame(height: 16)
                }
                .position(x: center.x, y: center.y - 4)

                HStack(spacing: 64) {
                    stepButton("minus") { pad.perform { try await $0.changeSpeed(by: -0.1) } }
                    stepButton("plus") { pad.perform { try await $0.changeSpeed(by: 0.1) } }
                }
                .position(x: center.x, y: size - 22)
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { g in
                        let dx = g.startLocation.x - center.x, dy = g.startLocation.y - center.y
                        let d = (dx * dx + dy * dy).squareRoot()
                        guard d > r - 40, d < r + 30, g.startLocation.y < size - 44 else { return }  // started on the ring
                        dragging = true
                        preview = min(speedAt(g.location, center: center), pad.speedLimit)
                    }
                    .onEnded { g in
                        defer { dragging = false; preview = nil }
                        guard dragging else { return }
                        let v = speedAt(g.location, center: center)
                        guard pad.connected, pad.isRunning else {
                            pad.say(pad.connected ? "Press Start first." : "The pad isn't connected. Check that it's on and nearby.")
                            return
                        }
                        if v > pad.speedLimit { pad.say(String(format: "Your max speed is %.1f mph", pad.speedLimit)) }
                        haptic()
                        let target = min(v, pad.speedLimit)
                        pad.perform { try await $0.setSpeed(target) }
                    }
            )
        }
        .frame(height: 300)
        .opacity(pad.connected ? 1 : 0.45)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Speed")
        .accessibilityValue(String(format: "%.1f miles per hour", speed))
        .accessibilityAdjustableAction { dir in
            pad.perform { try await $0.changeSpeed(by: dir == .increment ? 0.1 : -0.1) }
        }
    }

    private var hint: String {
        if preview != nil { return pad.isRunning ? "Release to set" : "Press Start first" }
        return pad.connected ? "" : "Pad not connected"
    }

    private func speedAt(_ pt: CGPoint, center: CGPoint) -> Double {
        var a = atan2(pt.y - center.y, pt.x - center.x) * 180 / .pi
        a = (a - Self.a0 + 720).truncatingRemainder(dividingBy: 360)
        if a > Self.span { a = a > Self.span + (360 - Self.span) / 2 ? 0 : Self.span }   // bottom gap snaps to the nearest end
        return ((FTMS.minMph + a / Self.span * (FTMS.maxMph - FTMS.minMph)) * 10).rounded() / 10
    }

    private func stepButton(_ icon: String, action: @escaping () -> Void) -> some View {
        Button { haptic(); action() } label: {
            Image(systemName: icon).font(.headline).frame(width: 52, height: 38)
        }
        .buttonStyle(.plain)
        .background(palette.card, in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(palette.line))
    }
}

// MARK: - Incline: tap or drag across the bars

struct InclineCard: View {
    @EnvironmentObject var pad: PadController
    let palette: Palette
    @State private var preview: Int?

    var body: some View {
        let inc = pad.connected ? Int(pad.inclinePct.rounded()) : 0
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .bottom) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Incline").font(.caption).foregroundStyle(palette.muted)
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(pad.connected ? "\(inc)" : "–").font(.system(size: 32, weight: .semibold, design: .rounded))
                        Text("%").foregroundStyle(palette.muted)
                        if let pv = preview, pv != inc { Text("→ \(pv)%").font(.caption).foregroundStyle(palette.muted) }
                    }
                }
                Spacer()
                HStack(spacing: 6) {
                    smallButton("minus") { pad.perform { try await $0.changeIncline(by: -1) } }
                    smallButton("plus") { pad.perform { try await $0.changeIncline(by: 1) } }
                }
            }
            GeometryReader { geo in
                HStack(alignment: .bottom, spacing: 4) {
                    ForEach(0...12, id: \.self) { i in
                        let on = pad.connected && i <= inc
                        let pre = preview.map { i <= $0 } ?? false
                        RoundedRectangle(cornerRadius: 4)
                            .fill(on ? palette.accent : (pre ? palette.accentSoft : palette.track))
                            .frame(height: geo.size.height * (0.18 + Double(i) / 12 * 0.82))
                    }
                }
                .frame(maxHeight: .infinity, alignment: .bottom)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { g in preview = index(at: g.location.x, width: geo.size.width) }
                        .onEnded { g in
                            let i = index(at: g.location.x, width: geo.size.width)
                            preview = nil
                            guard pad.connected, pad.isRunning else {
                                pad.say(pad.connected ? "Press Start first." : "The pad isn't connected. Check that it's on and nearby.")
                                return
                            }
                            haptic()
                            pad.perform { try await $0.setIncline(Double(i)) }
                        }
                )
            }
            .frame(height: 70)
            HStack {
                ForEach([0, 3, 6, 9, 12], id: \.self) { v in
                    Text("\(v)").font(.caption2).foregroundStyle(palette.muted)
                    if v != 12 { Spacer() }
                }
            }
        }
        .padding(14)
        .background(palette.card, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(palette.line))
        .opacity(pad.connected ? 1 : 0.45)
    }

    private func index(at x: CGFloat, width: CGFloat) -> Int {
        min(12, max(0, Int((x / max(width, 1)) * 13)))
    }

    private func smallButton(_ icon: String, action: @escaping () -> Void) -> some View {
        Button { haptic(); action() } label: {
            Image(systemName: icon).font(.subheadline.weight(.semibold)).frame(width: 44, height: 34)
        }
        .buttonStyle(.plain)
        .background(palette.bg, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(palette.line))
    }
}

// MARK: - Settings

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var pad: PadController
    @EnvironmentObject var health: HealthSync
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Appearance") {
                    Picker("Theme", selection: $model.theme) {
                        ForEach(ThemeChoice.allCases) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }
                Section {
                    Stepper(value: $pad.maxMph, in: FTMS.minMph...FTMS.maxMph, step: 0.1) {
                        Text(String(format: "Max speed  %.1f mph", pad.maxMph))
                    }
                } footer: {
                    Text("The app never goes faster, and pulls the remote back too.")
                }
                Section("Apple Health") {
                    if health.available {
                        Button("Allow saving walks to Health") { Task { await health.requestAccess() } }
                        if health.waitingCount > 0 {
                            Button("Save \(health.waitingCount) waiting walk\(health.waitingCount == 1 ? "" : "s") now") {
                                Task { await health.saveWaiting() }
                            }
                        }
                        if let err = health.lastError { Text(err).font(.footnote).foregroundStyle(.secondary) }
                    } else {
                        Text("Apple Health isn't available on this device.")
                    }
                }
                if !health.walks.isEmpty {
                    Section("Recent walks") {
                        ForEach(health.walks.prefix(10)) { w in
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(w.start, format: .dateTime.month().day().hour().minute())
                                    Text(String(format: "%.2f mi · ", w.distanceMiles) + fmtTime(w.durationS) + " · \(w.kcal) kcal")
                                        .font(.footnote).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: w.savedToHealth ? "checkmark.circle.fill" : "clock")
                                    .foregroundStyle(w.savedToHealth ? .green : .secondary)
                                    .accessibilityLabel(w.savedToHealth ? "Saved to Health" : "Waiting")
                            }
                        }
                    }
                }
                Section("Pad") {
                    LabeledContent("Device", value: pad.deviceName ?? "Not found yet")
                    LabeledContent("Status", value: pad.status)
                }
                Section {
                    Text("Unofficial app for the NovaWalk W50 TrekPad. Keep the pad's remote within reach.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}
