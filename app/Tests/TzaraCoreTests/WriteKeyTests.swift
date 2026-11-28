import Foundation
import Testing
@testable import TzaraCore

@Suite("WriteKeyDetector")
struct WriteKeyTests {
    private func gestures(_ inputs: [WriteKeyDetector.Input], key: WriteKey = .rightOption) -> [WriteKeyDetector.Gesture] {
        var detector = WriteKeyDetector(key: key)
        return inputs.compactMap { detector.handle($0) }
    }

    private func run(_ inputs: [WriteKeyDetector.Input], key: WriteKey = .rightOption) -> Int {
        gestures(inputs).filter { $0 == .tap }.count
    }

    @Test func aLongerPressIsAHold() {
        let found = gestures([.modifiers(keyCode: 61, held: .option, at: 0), .modifiers(keyCode: 61, held: [], at: 1.0)])
        #expect(found == [.hold])
    }

    @Test func aVeryLongPressIsNothing() {
        let found = gestures([.modifiers(keyCode: 61, held: .option, at: 0), .modifiers(keyCode: 61, held: [], at: 9)])
        #expect(found.isEmpty)
    }

    @Test func aQuickTapFires() {
        #expect(run([.modifiers(keyCode: 61, held: .option, at: 0), .modifiers(keyCode: 61, held: [], at: 0.15)]) == 1)
    }

    @Test func holdingIsNotATap() {
        #expect(run([.modifiers(keyCode: 61, held: .option, at: 0), .modifiers(keyCode: 61, held: [], at: 2)]) == 0)
    }

    @Test func theLeftKeyDoesNotFire() {
        #expect(run([.modifiers(keyCode: 58, held: .option, at: 0), .modifiers(keyCode: 58, held: [], at: 0.1)]) == 0)
    }

    @Test func aKeyPressedMeanwhileSpoilsIt() {
        #expect(run([.modifiers(keyCode: 61, held: .option, at: 0), .otherInput, .modifiers(keyCode: 61, held: [], at: 0.1)]) == 0)
    }

    @Test func anotherModifierSpoilsIt() {
        #expect(run([
            .modifiers(keyCode: 61, held: .option, at: 0),
            .modifiers(keyCode: 55, held: [.option, .command], at: 0.05),
            .modifiers(keyCode: 55, held: .option, at: 0.1),
            .modifiers(keyCode: 61, held: [], at: 0.12),
        ]) == 0)
    }

    @Test func itWorksAgainAfterASpoiledPress() {
        #expect(run([
            .modifiers(keyCode: 61, held: .option, at: 0), .otherInput, .modifiers(keyCode: 61, held: [], at: 0.1),
            .modifiers(keyCode: 61, held: .option, at: 1), .modifiers(keyCode: 61, held: [], at: 1.1),
        ]) == 1)
    }

    @Test func otherKeysCanBeChosen() {
        let found = gestures([.modifiers(keyCode: 54, held: .command, at: 0), .modifiers(keyCode: 54, held: [], at: 0.1)], key: .rightCommand)
        #expect(found == [.tap])
    }

    @Test func preferencesStartOnWithRightOption() {
        let defaults = UserDefaults(suiteName: "write-key-tests-\(UUID().uuidString)")!
        let preferences = WritePreferences(defaults: defaults)
        #expect(preferences.enabled)
        #expect(preferences.key == .rightOption)
        preferences.key = .rightControl
        #expect(WritePreferences(defaults: defaults).key == .rightControl)
    }

    @Test func locationIsOffUntilTheUserTurnsItOn() {
        let defaults = UserDefaults(suiteName: "location-tests-\(UUID().uuidString)")!
        let preferences = LocationPreferences(defaults: defaults)
        #expect(!preferences.enabled)
        preferences.enabled = true
        #expect(LocationPreferences(defaults: defaults).enabled)
    }
}
