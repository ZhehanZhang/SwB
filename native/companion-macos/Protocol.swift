import Foundation

enum CompanionFailure: Error {
    case invalidInput
}

let maxRecordBytes = 8192
let maxMessageBytes = 8192
let recordOverhead = 66
let maxWirePacketBytes = min(maxMessageBytes, (maxRecordBytes - recordOverhead
    - "{\"packet\":\"\",\"seq\":\(UInt64.max),\"type\":\"packet\"}".utf8.count) / 4 * 3)

func lowercaseHex(_ bytes: some Collection<UInt8>) -> Data? {
    guard bytes.count == 64 else { return nil }
    var result = Data()
    var high: UInt8 = 0
    for (index, byte) in bytes.enumerated() {
        let value: UInt8
        switch byte {
        case 48...57: value = byte - 48
        case 97...102: value = byte - 87
        default: return nil
        }
        if index.isMultiple(of: 2) {
            high = value << 4
        } else {
            result.append(high | value)
        }
    }
    return result
}

enum Field {
    case text(String)
    case integer(UInt64)
}

struct MessageParser {
    private let bytes: [UInt8]
    private let compact: Bool
    private var index = 0

    init(_ data: Data, compact: Bool) {
        self.bytes = Array(data)
        self.compact = compact
    }

    mutating func parse() throws -> [String: Field] {
        guard take(123) else { throw CompanionFailure.invalidInput }
        var fields: [String: Field] = [:]
        repeat {
            let key = try text()
            guard fields[key] == nil, take(58) else { throw CompanionFailure.invalidInput }
            skipSpace()
            if index < bytes.count, bytes[index] == 34 {
                fields[key] = .text(try text())
            } else {
                let start = index
                while index < bytes.count, (48...57).contains(bytes[index]) { index += 1 }
                guard start < index, bytes[start] != 48,
                      let number = UInt64(String(decoding: bytes[start..<index], as: UTF8.self))
                else { throw CompanionFailure.invalidInput }
                fields[key] = .integer(number)
            }
        } while take(44)
        guard take(125) else { throw CompanionFailure.invalidInput }
        skipSpace()
        guard index == bytes.count else { throw CompanionFailure.invalidInput }
        return fields
    }

    private mutating func text() throws -> String {
        guard take(34) else { throw CompanionFailure.invalidInput }
        let start = index - 1
        while index < bytes.count {
            let byte = bytes[index]
            index += 1
            if byte == 34 {
                let literal = Data(bytes[start..<index])
                guard String(data: literal, encoding: .utf8) != nil else {
                    throw CompanionFailure.invalidInput
                }
                return try JSONDecoder().decode(String.self, from: literal)
            }
            if byte == 92 { index += 1 }
        }
        throw CompanionFailure.invalidInput
    }

    private mutating func take(_ byte: UInt8) -> Bool {
        skipSpace()
        guard index < bytes.count, bytes[index] == byte else { return false }
        index += 1
        return true
    }

    private mutating func skipSpace() {
        if compact { return }
        while index < bytes.count {
            switch bytes[index] {
            case 9, 10, 13, 32: index += 1
            default: return
            }
        }
    }
}

enum Command {
    case status
    case authorize
    case start(String)
    case send(Data)
    case stop

    init(_ fields: [String: Field], expectedSequence: UInt64) throws {
        guard case .integer(expectedSequence)? = fields["seq"],
              case let .text(type)? = fields["type"] else {
            throw CompanionFailure.invalidInput
        }
        switch (type, fields.count) {
        case ("status", 2): self = .status
        case ("authorize", 2): self = .authorize
        case ("start", 3):
            guard case let .text(name)? = fields["name"] else {
                throw CompanionFailure.invalidInput
            }
            self = .start(name)
        case ("send", 3):
            guard case let .text(encoded)? = fields["packet"],
                  let packet = Data(base64Encoded: encoded),
                  !packet.isEmpty,
                  packet.base64EncodedString() == encoded else {
                throw CompanionFailure.invalidInput
            }
            self = .send(packet)
        case ("stop", 2): self = .stop
        default: throw CompanionFailure.invalidInput
        }
    }
}
