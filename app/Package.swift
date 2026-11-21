// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "TzaraApp",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "TzaraApp", targets: ["TzaraApp"]),
        .executable(name: "TzaraChatProbe", targets: ["TzaraChatProbe"]),
    ],
    targets: [
        // Everything that does not need a screen: easy to unit-test.
        .target(name: "TzaraCore"),
        // The menu-bar app: hotkey, floating chat box, settings.
        .executableTarget(name: "TzaraApp", dependencies: ["TzaraCore"]),
        // Streams one reply to the terminal, to check the client against a real server.
        .executableTarget(name: "TzaraChatProbe", dependencies: ["TzaraCore"]),
        .testTarget(name: "TzaraCoreTests", dependencies: ["TzaraCore"]),
    ]
)
