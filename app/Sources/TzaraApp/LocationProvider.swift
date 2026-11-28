import CoreLocation
import Foundation
import TzaraCore

/// Finds the city the user is in, when they have turned that on: one fix from the Mac's Location
/// Services, turned into "City, Country" by macOS. Only that name goes on; the coordinates never leave
/// this file. The answer is kept for half an hour: a person does not change city between messages.
@MainActor
final class LocationProvider: NSObject, CLLocationManagerDelegate {
    static let shared = LocationProvider()

    private let manager = CLLocationManager()
    private var cached: (place: String, at: Date)?
    private var fix: CheckedContinuation<CLLocation?, Never>?
    private static let keepFor: TimeInterval = 1800
    private static let waitFor: TimeInterval = 6

    override private init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyReduced  // a city does not need more
    }

    var enabled: Bool { LocationPreferences().enabled }

    /// Whether macOS has let Tzara use Location Services.
    var allowed: Bool {
        switch manager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse: return true
        default: return false
        }
    }

    var denied: Bool { manager.authorizationStatus == .denied || manager.authorizationStatus == .restricted }

    /// Asks macOS for permission (it shows its own question once).
    func requestPermission() {
        guard manager.authorizationStatus == .notDetermined else { return }
        manager.requestWhenInUseAuthorization()
        FileLog.shared.info("location: asked for permission")
    }

    /// The city and country the user is in, or `nil` if it is off, not allowed or not found.
    func place() async -> String? {
        guard enabled else { return nil }
        if let cached, Date().timeIntervalSince(cached.at) < Self.keepFor { return cached.place }
        guard allowed else {
            requestPermission()
            return nil
        }
        guard let location = await oneFix() else {
            FileLog.shared.info("location: no fix")
            return nil
        }
        let found = try? await CLGeocoder().reverseGeocodeLocation(location).first
        let parts = [found?.locality, found?.country].compactMap { $0 }.filter { !$0.isEmpty }
        guard !parts.isEmpty else { return nil }
        let place = parts.joined(separator: ", ")
        cached = (place, Date())
        FileLog.shared.info("location: found a city")
        return place
    }

    func forget() { cached = nil }

    private func oneFix() async -> CLLocation? {
        await withCheckedContinuation { continuation in
            fix = continuation
            manager.requestLocation()
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.waitFor) { [weak self] in
                self?.finish(nil)  // no answer in time
            }
        }
    }

    private func finish(_ location: CLLocation?) {
        guard let fix else { return }
        self.fix = nil
        fix.resume(returning: location)
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        let last = locations.last
        Task { @MainActor in self.finish(last) }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in self.finish(nil) }
    }
}
