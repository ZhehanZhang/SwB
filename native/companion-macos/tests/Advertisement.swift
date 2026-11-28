import CoreBluetooth
import Foundation

@main
enum AdvertisementTest {
    static func main() {
        let name = "MuseGadgetA1B2C3"
        guard let profile = Profile(name: name) else { fatalError("The Muse gadget name was rejected") }
        let advertisement = profile.advertisement
        precondition(advertisement.count == 2 && advertisement[CBAdvertisementDataLocalNameKey] as? String == name)
        precondition(advertisement[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] == [profile.service])
        let nameBytes = 3 + 2 + name.utf8.count
        precondition(nameBytes == 21 && nameBytes + 18 == 39 && nameBytes + 18 > 31)
        precondition(profile.service.uuidString == "7FDD3D1C-38EA-46CF-8B46-314ECF5F240C")
        precondition(profile.rx.uuidString == "4D593029-28A2-4A6E-A1F0-3C2D5E8F9B01")
        precondition(profile.tx.uuidString == "D75DC4CA-7B2B-4E9C-8F0A-1D2E3F4A5B6C")

        let (service, tx) = profile.makeService()
        guard let characteristics = service.characteristics as? [CBMutableCharacteristic],
              characteristics.count == 2 else { fatalError("Expected only Link RX and TX") }
        let rx = characteristics[0]
        precondition(service.isPrimary && service.uuid == profile.service && characteristics[1] === tx)
        precondition(rx.uuid == profile.rx && rx.properties == [.write, .writeWithoutResponse])
        precondition(tx.uuid == profile.tx && tx.properties == [.read, .notify])
        precondition(rx.permissions == .writeable && tx.permissions == .readable)
        precondition((rx.descriptors ?? []).isEmpty && (tx.descriptors ?? []).isEmpty)

        let central = UUID()
        let other = UUID()
        let first = Data([0xfe, 0x00, 0x02, 0x7b])
        let last = Data([0xfe, 0x01, 0x02, 0x7d])
        var value = TXValue()
        precondition(value.read(offset: 0, from: central) == Data())
        value.sent(first, to: central)
        precondition(value.read(offset: 0, from: central) == first)
        value.sent(last, to: central)
        precondition(value.read(offset: 0, from: central) == last)
        precondition(value.read(offset: 3, from: central) == Data([0x7d]))
        precondition(value.read(offset: 5, from: central) == nil)
        precondition(value.read(offset: 0, from: other) == Data())
        value = TXValue()
        precondition(value.read(offset: 0, from: central) == Data())
    }
}
