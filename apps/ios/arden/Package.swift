// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "ArdenMobile",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [
        .library(name: "ArdenCore", targets: ["ArdenCore"])
    ],
    targets: [
        .target(
            name: "ArdenCore",
            path: "Arden/Core"
        ),
        .testTarget(
            name: "ArdenCoreTests",
            dependencies: ["ArdenCore"],
            path: "Tests/ArdenCoreTests"
        )
    ]
)
