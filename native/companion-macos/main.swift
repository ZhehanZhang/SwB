import AppKit
import Darwin

do {
    guard CommandLine.arguments.count == 2 else { throw CompanionFailure.invalidInput }
    let channel = try SocketChannel(Bootstrap(path: CommandLine.arguments[1]))
    let peripheral = Peripheral(channel: channel)
    channel.onCommand = { [weak peripheral] in peripheral?.handle($0) }
    channel.onClose = { [weak peripheral] code in
        peripheral?.stop()
        exit(code)
    }
    withExtendedLifetime(peripheral) {
        let app = NSApplication.shared
        channel.start()
        app.run()
    }
} catch {
    exit(1)
}
