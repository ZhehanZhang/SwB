import Darwin
import Foundation

struct Bootstrap {
    let socketPath: String
    let token: Data

    init(path: String) throws {
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        guard path.hasPrefix("/"), !path.utf8.contains(0), parts.count >= 3,
              parts.dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }),
              let fileName = parts.last else { throw CompanionFailure.invalidInput }
        let directory = parts.dropLast().joined(separator: "/")
        let dir = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard dir >= 0 else { throw CompanionFailure.invalidInput }
        defer { close(dir) }
        var dirInfo = stat()
        guard fstat(dir, &dirInfo) == 0, privateFile(dirInfo, type: mode_t(S_IFDIR), mode: 0o700) else {
            throw CompanionFailure.invalidInput
        }
        let name = String(fileName)
        let file = openat(dir, name, O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC)
        guard file >= 0 else { throw CompanionFailure.invalidInput }
        defer { close(file) }
        var info = stat()
        guard fstat(file, &info) == 0, privateFile(info, type: mode_t(S_IFREG), mode: 0o600),
              info.st_nlink == 1, (1...Int64(maxRecordBytes)).contains(info.st_size) else {
            throw CompanionFailure.invalidInput
        }
        var bytes = [UInt8](repeating: 0, count: Int(info.st_size) + 1)
        var count = 0
        while count < bytes.count {
            let readCount = bytes.withUnsafeMutableBytes {
                read(file, $0.baseAddress!.advanced(by: count), $0.count - count)
            }
            if readCount == 0 { break }
            if readCount < 0 {
                if errno == EINTR { continue }
                throw CompanionFailure.invalidInput
            }
            count += readCount
        }
        var entry = stat()
        guard fstatat(dir, name, &entry, AT_SYMLINK_NOFOLLOW) == 0,
              entry.st_dev == info.st_dev, entry.st_ino == info.st_ino,
              privateFile(entry, type: mode_t(S_IFREG), mode: 0o600), entry.st_nlink == 1,
              unlinkat(dir, name, 0) == 0, count == Int(info.st_size) else {
            throw CompanionFailure.invalidInput
        }
        var parser = MessageParser(Data(bytes.prefix(count)), compact: false)
        let fields = try parser.parse()
        let expectedSocketPath = directory + "/s"
        guard fields.count == 2, case let .text(socketPath)? = fields["socket"],
              socketPath.utf8.elementsEqual(expectedSocketPath.utf8),
              case let .text(hex)? = fields["token"], let token = lowercaseHex(hex.utf8) else {
            throw CompanionFailure.invalidInput
        }
        self.socketPath = expectedSocketPath
        self.token = token
    }

    func connectSocket() throws -> Int32 {
        var address = sockaddr_un()
        let path = Array(socketPath.utf8) + [0]
        guard path.count <= MemoryLayout.size(ofValue: address.sun_path) else {
            throw CompanionFailure.invalidInput
        }
        var info = stat()
        guard lstat(socketPath, &info) == 0, privateFile(info, type: mode_t(S_IFSOCK), mode: 0o600) else {
            throw CompanionFailure.invalidInput
        }
        let length = socklen_t(MemoryLayout.size(ofValue: address.sun_len)
                             + MemoryLayout.size(ofValue: address.sun_family) + path.count)
        address.sun_len = UInt8(length)
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: path) }
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw CompanionFailure.invalidInput }
        var connected = false
        defer { if !connected { close(fd) } }
        var noSignal: Int32 = 1
        guard fcntl(fd, F_SETFD, FD_CLOEXEC) != -1, fcntl(fd, F_SETFL, O_NONBLOCK) != -1,
              setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size)) == 0
        else { throw CompanionFailure.invalidInput }
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, length)
            }
        }
        if result != 0 {
            guard errno == EINPROGRESS else { throw CompanionFailure.invalidInput }
            var event = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
            var socketError: Int32 = 0
            var size = socklen_t(MemoryLayout<Int32>.size)
            guard poll(&event, 1, 3000) == 1,
                  getsockopt(fd, SOL_SOCKET, SO_ERROR, &socketError, &size) == 0, socketError == 0
            else { throw CompanionFailure.invalidInput }
        }
        var uid: uid_t = 0
        var gid: gid_t = 0
        guard getpeereid(fd, &uid, &gid) == 0, uid == getuid() else {
            throw CompanionFailure.invalidInput
        }
        connected = true
        return fd
    }
}

private func privateFile(_ info: stat, type: mode_t, mode: mode_t) -> Bool {
    info.st_uid == getuid() && (info.st_mode & mode_t(S_IFMT)) == type
        && (info.st_mode & 0o7777) == mode
}
