import SwiftUI
import AVFoundation

// Full-screen QR scanner. Wraps an AVCaptureSession + AVCaptureMetadataOutput
// (.qr) via a UIViewControllerRepresentable. Calls `onScan` with the decoded
// string and dismisses. Direction B chrome: dark scrim, rounded viewfinder
// cutout, caption, Cancel button. Handles the no-permission case gracefully.
struct QRScannerView: View {
    let onScan: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var authorization: AVAuthorizationStatus = AVCaptureDevice.authorizationStatus(for: .video)
    @State private var didScan = false

    private let viewfinder: CGFloat = 248

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            switch authorization {
            case .authorized:
                scanner
            case .notDetermined:
                Color.clear
                    .task { await requestAccess() }
            default:
                permissionDenied
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Scanner

    private var scanner: some View {
        ZStack {
            CameraPreview { code in
                guard !didScan else { return }
                didScan = true
                let haptics = UINotificationFeedbackGenerator()
                haptics.notificationOccurred(.success)
                onScan(code)
                dismiss()
            }
            .ignoresSafeArea()

            scrim

            VStack(spacing: 18) {
                Spacer()

                viewfinderCutout

                Text("Point at the QR on your computer")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundStyle(Theme.textPrimary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)

                Spacer()

                cancelButton
                    .padding(.bottom, 12)
            }
        }
    }

    // Dark scrim with a transparent rounded window punched out over the
    // viewfinder so the camera reads clearly there but is dimmed elsewhere.
    private var scrim: some View {
        Rectangle()
            .fill(Color.black.opacity(0.55))
            .reverseMask {
                RoundedRectangle(cornerRadius: 28, style: .continuous)
                    .frame(width: viewfinder, height: viewfinder)
            }
            .ignoresSafeArea()
    }

    private var viewfinderCutout: some View {
        RoundedRectangle(cornerRadius: 28, style: .continuous)
            .stroke(Theme.accent, lineWidth: 2)
            .frame(width: viewfinder, height: viewfinder)
    }

    private var cancelButton: some View {
        Button("Cancel") { dismiss() }
            .font(.system(size: 17, weight: .semibold))
            .foregroundStyle(Theme.pillText)
            .padding(.horizontal, 28)
            .padding(.vertical, 13)
            .background(Theme.pill, in: Capsule())
            .buttonStyle(PressScaleButtonStyle())
            .accessibilityLabel("Cancel")
    }

    // MARK: - Permission denied

    private var permissionDenied: some View {
        VStack(spacing: 16) {
            Image(systemName: "video.slash")
                .font(.system(size: 34, weight: .regular))
                .foregroundStyle(Theme.textSecondary)

            Text("Camera access is off")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)

            Text("Enable camera access in Settings to scan the pairing code on your computer.")
                .font(.system(size: 15))
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)

            VStack(spacing: 0) {
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(Theme.accent)
                .frame(height: 44)
                .buttonStyle(PressScaleButtonStyle())

                Button("Cancel") { dismiss() }
                    .font(.system(size: 17))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(height: 44)
                    .buttonStyle(PressScaleButtonStyle())
            }
            .padding(.top, 8)
        }
        .padding(24)
    }

    private func requestAccess() async {
        let granted = await AVCaptureDevice.requestAccess(for: .video)
        authorization = granted ? .authorized : .denied
    }
}

// MARK: - Camera preview (AVFoundation bridge)

private struct CameraPreview: UIViewControllerRepresentable {
    let onScan: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerViewController {
        let controller = ScannerViewController()
        controller.onScan = onScan
        return controller
    }

    func updateUIViewController(_ controller: ScannerViewController, context: Context) {
        controller.onScan = onScan
    }
}

final class ScannerViewController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onScan: ((String) -> Void)?

    private let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "arden.qr.session")
    private var previewLayer: AVCaptureVideoPreviewLayer?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        configureSession()
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.bounds
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        startRunning()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        sessionQueue.async { [session] in
            if session.isRunning { session.stopRunning() }
        }
    }

    private func configureSession() {
        guard
            let device = AVCaptureDevice.default(for: .video),
            let input = try? AVCaptureDeviceInput(device: device),
            session.canAddInput(input)
        else { return }

        session.beginConfiguration()
        session.addInput(input)

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else {
            session.commitConfiguration()
            return
        }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]
        session.commitConfiguration()

        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        layer.frame = view.bounds
        view.layer.addSublayer(layer)
        previewLayer = layer
    }

    private func startRunning() {
        sessionQueue.async { [session] in
            if !session.isRunning { session.startRunning() }
        }
    }

    func metadataOutput(
        _ output: AVCaptureMetadataOutput,
        didOutput metadataObjects: [AVMetadataObject],
        from connection: AVCaptureConnection
    ) {
        guard
            let object = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
            object.type == .qr,
            let value = object.stringValue
        else { return }

        session.stopRunning()
        onScan?(value)
    }
}

// MARK: - Pairing URL parsing

// Parses `arden://connect?url=...&key=...` into its URL-decoded components.
// Returns nil when the scheme/host is wrong or either field is missing/empty.
func parseArdenPairing(_ raw: String) -> (url: String, key: String)? {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard
        let components = URLComponents(string: trimmed),
        ["arden", "ntrp"].contains(components.scheme?.lowercased() ?? ""),
        components.host?.lowercased() == "connect",
        let items = components.queryItems
    else { return nil }

    func value(_ name: String) -> String? {
        items.first { $0.name == name }?.value.flatMap { $0.isEmpty ? nil : $0 }
    }

    guard let url = value("url"), let key = value("key") else { return nil }
    return (url: url, key: key)
}

// MARK: - Reverse mask helper

private extension View {
    // Punches a transparent hole through `self` in the shape of `mask`.
    func reverseMask<Mask: View>(@ViewBuilder _ mask: () -> Mask) -> some View {
        self.mask {
            Rectangle()
                .overlay(alignment: .center) {
                    mask()
                        .blendMode(.destinationOut)
                }
                .compositingGroup()
        }
    }
}

// MARK: - Preview

// Camera won't run in the canvas; this exercises the chrome layout only.
#Preview {
    QRScannerView { _ in }
}
