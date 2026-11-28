import CryptoKit
import Darwin
import Dispatch
import Foundation

final class SocketChannel {
    private let descriptor: Int32
    private let key: SymmetricKey
    private var received: UInt64 = 0
    private var sent: UInt64 = 0
    private var record: [UInt8] = []
    private var pending = Data()
    private var reader: DispatchSourceRead?
    private var writer: DispatchSourceWrite?
    private var readDeadline: DispatchWorkItem?
    private var writeDeadline: DispatchWorkItem?
    private var closed = false

    var onCommand: ((Command) -> Void)?
    var onClose: ((Int32) -> Void)?

    init(_ bootstrap: Bootstrap) throws {
        descriptor = try bootstrap.connectSocket()
        key = SymmetricKey(data: bootstrap.token)
        record.reserveCapacity(maxRecordBytes)
    }

    func start() {
        let source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: .main)
        source.setEventHandler { [weak self] in self?.readAvailable() }
        reader = source
        source.resume()
        send("ready")
    }

    func close(_ code: Int32) {
        guard !closed else { return }
        closed = true
        readDeadline?.cancel()
        writeDeadline?.cancel()
        reader?.cancel()
        writer?.cancel()
        Darwin.close(descriptor)
        onClose?(code)
    }

    func send(_ type: String, fields: [String: String] = [:]) {
        guard !closed else { return }
        var message: [String: Any] = fields
        guard sent < UInt64.max else { close(1); return }
        message["seq"] = sent + 1
        message["type"] = type
        guard let payload = try? JSONSerialization.data(withJSONObject: message, options: [.sortedKeys, .withoutEscapingSlashes]),
              payload.count + recordOverhead <= maxRecordBytes,
              pending.count + payload.count + recordOverhead <= 4 * maxRecordBytes else {
            close(1)
            return
        }
        let mac = HMAC<SHA256>.authenticationCode(for: Data("app:".utf8) + payload, using: key)
        let hex = mac.map { String(format: "%02x", $0) }.joined()
        pending.append(contentsOf: hex.utf8)
        pending.append(32)
        pending.append(payload)
        pending.append(10)
        sent += 1
        flush()
    }

    private func readAvailable() {
        guard !closed else { return }
        var bytes = [UInt8](repeating: 0, count: 4096)
        let count = bytes.withUnsafeMutableBytes { recv(descriptor, $0.baseAddress, $0.count, 0) }
        if count == 0 { close(record.isEmpty ? 0 : 1); return }
        if count < 0 {
            if errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK { close(1) }
            return
        }
        for byte in bytes.prefix(count) {
            if byte == 10 {
                do {
                    let command = try decodeRecord()
                    record.removeAll(keepingCapacity: true)
                    readDeadline?.cancel()
                    readDeadline = nil
                    onCommand?(command)
                    if closed { return }
                } catch {
                    close(1)
                    return
                }
            } else {
                guard record.count < maxRecordBytes - 1 else { close(1); return }
                record.append(byte)
            }
        }
        if !record.isEmpty, readDeadline == nil {
            let deadline = DispatchWorkItem { [weak self] in self?.close(1) }
            readDeadline = deadline
            DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: deadline)
        }
    }

    private func decodeRecord() throws -> Command {
        guard record.count > 65, record[64] == 32, received < UInt64.max,
              let mac = lowercaseHex(record.prefix(64)) else {
            throw CompanionFailure.invalidInput
        }
        let payload = Data(record.dropFirst(65))
        guard HMAC<SHA256>.isValidAuthenticationCode(mac, authenticating: Data("py:".utf8) + payload, using: key)
        else { throw CompanionFailure.invalidInput }
        var parser = MessageParser(payload, compact: true)
        let command = try Command(parser.parse(), expectedSequence: received + 1)
        received += 1
        return command
    }

    private func flush() {
        guard !closed else { return }
        if !pending.isEmpty {
            let count = pending.withUnsafeBytes { Darwin.send(descriptor, $0.baseAddress, $0.count, 0) }
            if count > 0 {
                pending.removeFirst(count)
            } else if count == 0 || (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) {
                close(1)
                return
            }
        }
        if pending.isEmpty {
            writer?.cancel()
            writer = nil
            writeDeadline?.cancel()
            writeDeadline = nil
        } else if writer == nil {
            let source = DispatchSource.makeWriteSource(fileDescriptor: descriptor, queue: .main)
            source.setEventHandler { [weak self] in self?.flush() }
            writer = source
            source.resume()
            let deadline = DispatchWorkItem { [weak self] in self?.close(1) }
            writeDeadline = deadline
            DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: deadline)
        }
    }
}
