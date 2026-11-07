import Foundation
import HealthKit

/// Saves walks to Apple Health as indoor walking workouts, with per-minute distance and
/// active energy. Walks are kept on the phone (UserDefaults) until saved, so nothing is lost
/// if Health access is off or a save fails; they're retried on launch and when access is given.
@MainActor
final class HealthSync: ObservableObject {
    @Published private(set) var walks: [Walk] = []
    @Published private(set) var lastError: String?

    private let store = HKHealthStore()
    private let key = "walks.v1"

    var available: Bool { HKHealthStore.isHealthDataAvailable() }
    var waitingCount: Int { walks.filter { !$0.savedToHealth }.count }

    init() {
        if let data = UserDefaults.standard.data(forKey: key),
           let saved = try? JSONDecoder().decode([Walk].self, from: data) {
            walks = saved
        }
    }

    private var shareTypes: Set<HKSampleType> {
        [HKObjectType.workoutType(), HKQuantityType(.distanceWalkingRunning), HKQuantityType(.activeEnergyBurned)]
    }

    /// Shows Apple's permission sheet the first time; later calls return at once.
    func requestAccess() async {
        guard available else { return }
        do {
            try await store.requestAuthorization(toShare: shareTypes, read: [])
        } catch {
            lastError = error.localizedDescription
        }
    }

    var canSave: Bool {
        available && store.authorizationStatus(for: HKObjectType.workoutType()) == .sharingAuthorized
    }

    func add(_ walk: Walk) {
        walks.insert(walk, at: 0)
        if walks.count > 200 { walks.removeLast(walks.count - 200) }
        persist()
        Task { await saveWaiting() }
    }

    func saveWaiting() async {
        guard available, waitingCount > 0 else { return }   // only ask for Health access once there's a walk to save
        if !canSave { await requestAccess() }
        guard canSave else { return }
        for i in walks.indices where !walks[i].savedToHealth {
            do {
                try await save(walks[i])
                walks[i].savedToHealth = true
                lastError = nil
            } catch {
                lastError = "Couldn't save to Health: \(error.localizedDescription)"
            }
        }
        persist()
    }

    private func save(_ walk: Walk) async throws {
        let config = HKWorkoutConfiguration()
        config.activityType = .walking
        config.locationType = .indoor
        let builder = HKWorkoutBuilder(healthStore: store, configuration: config, device: .local())
        try await builder.beginCollection(at: walk.start)
        var samples: [HKSample] = []
        let segments = walk.segments.isEmpty
            ? [Walk.Segment(start: walk.start, end: walk.end, distanceM: walk.distanceM, kcal: walk.kcal)]
            : walk.segments
        for seg in segments where seg.end > seg.start {
            if seg.distanceM > 0 {
                samples.append(HKQuantitySample(type: HKQuantityType(.distanceWalkingRunning),
                                                quantity: HKQuantity(unit: .meter(), doubleValue: Double(seg.distanceM)),
                                                start: seg.start, end: seg.end))
            }
            if seg.kcal > 0 {
                samples.append(HKQuantitySample(type: HKQuantityType(.activeEnergyBurned),
                                                quantity: HKQuantity(unit: .kilocalorie(), doubleValue: Double(seg.kcal)),
                                                start: seg.start, end: seg.end))
            }
        }
        if !samples.isEmpty { try await builder.addSamples(samples) }
        try await builder.addMetadata([
            HKMetadataKeyIndoorWorkout: true,
            "TrekPadMaxInclinePercent": walk.maxInclinePct,
            "TrekPadAverageInclinePercent": walk.avgInclinePct,
        ])
        try await builder.endCollection(at: walk.end)
        _ = try await builder.finishWorkout()
    }

    private func persist() {
        if let data = try? JSONEncoder().encode(walks) { UserDefaults.standard.set(data, forKey: key) }
    }
}
