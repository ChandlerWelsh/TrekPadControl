import SwiftUI

@main
struct TrekPadApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .environmentObject(model.pad)
                .environmentObject(model.recorder)
                .environmentObject(model.health)
                .preferredColorScheme(model.theme.colorScheme)
        }
    }
}

enum ThemeChoice: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}

/// Wires the pad, the walk recorder and Health together.
@MainActor
final class AppModel: ObservableObject {
    let pad = PadController()
    let recorder = WalkRecorder()
    let health = HealthSync()
    @Published var theme: ThemeChoice {
        didSet { UserDefaults.standard.set(theme.rawValue, forKey: "theme") }
    }
    private var timer: Timer?

    init() {
        theme = ThemeChoice(rawValue: UserDefaults.standard.string(forKey: "theme") ?? "") ?? .system
        pad.onSample = { [weak self] rec in self?.recorder.feed(rec) }
        recorder.onFinish = { [weak self] walk in self?.health.add(walk) }
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.recorder.tick() }
        }
        Task { await health.saveWaiting() }
    }
}
