import CoreBluetooth

@main
enum AdvertisementTest {
    static func main() {
        let name = "MuseGadgetA1B2C3"
        guard let profile = Profile(name: name) else { fatalError("The Muse gadget name was rejected") }
        let advertisement = profile.advertisement
        precondition(advertisement.count == 1 && advertisement[CBAdvertisementDataLocalNameKey] as? String == name)
        let nameBytes = 3 + 2 + name.utf8.count
        precondition(nameBytes <= 31 && nameBytes + 18 > 31)
        precondition(profile.service.uuidString == "7FDD3D1C-38EA-46CF-8B46-314ECF5F240C")
        precondition(profile.rx.uuidString == "4D593029-28A2-4A6E-A1F0-3C2D5E8F9B01")
        precondition(profile.tx.uuidString == "D75DC4CA-7B2B-4E9C-8F0A-1D2E3F4A5B6C")
    }
}
