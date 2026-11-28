import CoreBluetooth
import Dispatch
import Foundation
import os

private let maxNotificationBytes = 160

struct Profile {
    let name: String
    let service: CBUUID
    let rx: CBUUID
    let tx: CBUUID

    var advertisement: [String: Any] {
        [CBAdvertisementDataLocalNameKey: name]
    }

    func makeService() -> (service: CBMutableService, tx: CBMutableCharacteristic) {
        let rx = CBMutableCharacteristic(type: self.rx, properties: [.write, .writeWithoutResponse],
                                         value: nil, permissions: .writeable)
        let tx = CBMutableCharacteristic(type: self.tx, properties: [.read, .notify],
                                         value: nil, permissions: .readable)
        let service = CBMutableService(type: self.service, primary: true)
        service.characteristics = [rx, tx]
        return (service, tx)
    }

    init?(name: String) {
        self.name = name
        #if SWB_BLE_SMOKE_TEST
        if name == "SwB BLE Test" {
            service = CBUUID(string: "31E6E800-189C-4B55-AECC-974501977A01")
            rx = CBUUID(string: "31E6E800-189C-4B55-AECC-974501977A02")
            tx = CBUUID(string: "31E6E800-189C-4B55-AECC-974501977A03")
            return
        }
        #endif
        let prefix = "MuseGadget"
        guard name.hasPrefix(prefix), name.utf8.count == prefix.utf8.count + 6,
              name.utf8.suffix(6).allSatisfy({ (48...57).contains($0) || (65...70).contains($0) })
        else { return nil }
        service = CBUUID(string: "7fdd3d1c-38ea-46cf-8b46-314ecf5f240c")
        rx = CBUUID(string: "4d593029-28a2-4a6e-a1f0-3c2d5e8f9b01")
        tx = CBUUID(string: "d75dc4ca-7b2b-4e9c-8f0a-1d2e3f4a5b6c")
    }
}

struct TXValue {
    private var packet = Data()
    private var recipient: UUID?

    mutating func sent(_ packet: Data, to central: UUID) {
        self.packet = packet
        recipient = central
    }

    func read(offset: Int, from central: UUID) -> Data? {
        let value = recipient == central ? packet : Data()
        guard (0...value.count).contains(offset) else { return nil }
        return value.dropFirst(offset)
    }
}

final class Peripheral: NSObject, CBPeripheralManagerDelegate {
    private enum Phase {
        case idle, waiting, addingService, advertising, running
    }

    private let channel: SocketChannel
    private var manager: CBPeripheralManager?
    private var phase = Phase.idle
    private var startRequested = false
    private var authorizationDeadline: DispatchTime?
    private var profile: Profile?
    private var tx: CBMutableCharacteristic?
    private var txValue = TXValue()
    private var loggedTXRead = false
    private let log = Logger(subsystem: "one.swb.companion", category: "Pairing")
    private var central: CBCentral?
    private var subscriptionDeadline: DispatchWorkItem?
    private var subscribed = false
    private var pendingWrites: [Data] = []
    private var pendingNotifications: [Data] = []

    init(channel: SocketChannel) {
        self.channel = channel
    }

    func handle(_ command: Command) {
        switch command {
        case .status:
            channel.send("status", fields: ["permission": permission])
        case .authorize:
            guard authorizationDeadline == nil else { channel.close(1); return }
            if permission == "Approval required" {
                authorizationDeadline = .now() + 55
                ensureManager()
                finishAuthorization()
            } else {
                channel.send("status", fields: ["permission": permission])
            }
        case .start(let name):
            guard let profile = Profile(name: name) else { channel.close(1); return }
            guard !startRequested, CBManager.authorization == .allowedAlways else {
                channel.send("error")
                return
            }
            startRequested = true
            self.profile = profile
            phase = .waiting
            ensureManager()
            advanceStart()
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                guard let self, self.phase != .idle, self.phase != .running else { return }
                self.fail()
            }
        case .send(let packet):
            if phase == .running && central == nil { return }
            guard phase == .running, (1...maxNotificationBytes).contains(packet.count),
                  pendingNotifications.count < 256 else {
                fail()
                return
            }
            pendingNotifications.append(packet)
            flushNotifications()
        case .stop:
            channel.close(0)
        }
    }

    func stop() {
        let previous = phase
        phase = .idle
        authorizationDeadline = nil
        profile = nil
        tx = nil
        clearCentral()
        if let manager, manager.state == .poweredOn {
            if previous == .advertising || previous == .running { manager.stopAdvertising() }
            if previous != .idle && previous != .waiting { manager.removeAllServices() }
        }
        manager?.delegate = nil
        manager = nil
    }

    private func clearCentral() {
        subscriptionDeadline?.cancel()
        subscriptionDeadline = nil
        central = nil
        subscribed = false
        txValue = TXValue()
        loggedTXRead = false
        pendingWrites.removeAll()
        pendingNotifications.removeAll()
    }

    private var permission: String {
        switch CBManager.authorization {
        case .allowedAlways: return manager?.state == .unsupported ? "Unavailable" : "Allowed"
        case .denied: return "Denied"
        case .restricted: return "Restricted"
        case .notDetermined: return "Approval required"
        @unknown default: return "Unavailable"
        }
    }

    private func ensureManager() {
        if manager == nil {
            manager = CBPeripheralManager(delegate: self, queue: .main,
                                          options: [CBPeripheralManagerOptionShowPowerAlertKey: false])
        }
    }

    private func finishAuthorization() {
        guard let deadline = authorizationDeadline else { return }
        let status = permission
        if status != "Approval required" {
            authorizationDeadline = nil
            channel.send("status", fields: ["permission": status])
        } else if manager?.state == .unsupported {
            authorizationDeadline = nil
            channel.send("status", fields: ["permission": "Unavailable"])
        } else if .now() >= deadline {
            channel.close(1)
        } else {
            DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(250)) { [weak self] in
                self?.finishAuthorization()
            }
        }
    }

    private func advanceStart() {
        guard phase == .waiting, let manager, let profile else { return }
        switch manager.state {
        case .unknown, .resetting: return
        case .poweredOn:
            let (service, tx) = profile.makeService()
            self.tx = tx
            phase = .addingService
            manager.add(service)
        default: fail()
        }
    }

    private func fail() {
        let hadCentral = central != nil
        stop()
        if hadCentral { channel.send("disconnect") }
        channel.send("error")
    }

    private func flushNotifications() {
        guard subscribed, let manager, let central, let tx else { return }
        while let packet = pendingNotifications.first {
            guard packet.count <= central.maximumUpdateValueLength else { fail(); return }
            guard manager.updateValue(packet, for: tx, onSubscribedCentrals: [central]) else { return }
            txValue.sent(packet, to: central.identifier)
            pendingNotifications.removeFirst()
        }
    }

    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        guard peripheral === manager else { return }
        if phase == .waiting {
            advanceStart()
        } else if phase != .idle && peripheral.state != .poweredOn {
            fail()
        }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        guard peripheral === manager, phase == .addingService,
              let profile, service.uuid == profile.service else { return }
        guard error == nil else { fail(); return }
        phase = .advertising
        peripheral.startAdvertising(profile.advertisement)
    }

    func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        guard peripheral === manager, phase == .advertising else { return }
        guard error == nil else { fail(); return }
        phase = .running
        channel.send("started")
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral,
                           didSubscribeTo characteristic: CBCharacteristic) {
        guard peripheral === manager, phase == .running, characteristic.uuid == tx?.uuid,
              self.central == nil || self.central?.identifier == central.identifier else { return }
        subscriptionDeadline?.cancel()
        subscriptionDeadline = nil
        self.central = central
        subscribed = true
        let mtu = min(central.maximumUpdateValueLength, maxNotificationBytes) + 3
        channel.send("mtu", fields: ["mtu": String(mtu)])
        for packet in pendingWrites {
            channel.send("packet", fields: ["packet": packet.base64EncodedString()])
        }
        pendingWrites.removeAll()
        flushNotifications()
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral,
                           didUnsubscribeFrom characteristic: CBCharacteristic) {
        guard peripheral === manager, characteristic.uuid == tx?.uuid,
              central.identifier == self.central?.identifier else { return }
        clearCentral()
        channel.send("disconnect")
    }

    func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) {
        guard peripheral === manager, phase == .running else { return }
        flushNotifications()
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        guard peripheral === manager, phase == .running, request.characteristic.uuid == tx?.uuid
        else { peripheral.respond(to: request, withResult: .readNotPermitted); return }
        guard let value = txValue.read(offset: request.offset, from: request.central.identifier)
        else { peripheral.respond(to: request, withResult: .invalidOffset); return }
        if !loggedTXRead {
            log.info("Muse BLE TX read (offset: \(request.offset, privacy: .public), bytes: \(value.count, privacy: .public))")
            loggedTXRead = true
        }
        request.value = value
        peripheral.respond(to: request, withResult: .success)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        guard let first = requests.first else { return }
        let central = self.central ?? first.central
        guard peripheral === manager, phase == .running, let profile,
              requests.allSatisfy({ $0.central.identifier == central.identifier && $0.characteristic.uuid == profile.rx })
        else { peripheral.respond(to: first, withResult: .writeNotPermitted); return }
        guard requests.allSatisfy({ $0.offset == 0 }) else {
            peripheral.respond(to: first, withResult: .invalidOffset)
            return
        }
        guard requests.count <= 32,
              requests.allSatisfy({ $0.value.map { !$0.isEmpty && $0.count <= maxWirePacketBytes } ?? false }),
              subscribed || pendingWrites.count + requests.count <= 32 else {
            peripheral.respond(to: first, withResult: .invalidAttributeValueLength)
            return
        }
        if self.central == nil {
            self.central = central
            let deadline = DispatchWorkItem { [weak self] in
                guard let self, !self.subscribed else { return }
                self.clearCentral()
                self.channel.send("disconnect")
            }
            subscriptionDeadline = deadline
            DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: deadline)
        }
        for request in requests {
            if let packet = request.value {
                if subscribed {
                    channel.send("packet", fields: ["packet": packet.base64EncodedString()])
                } else {
                    pendingWrites.append(packet)
                }
            }
        }
        peripheral.respond(to: first, withResult: .success)
    }
}
