/// 純資料型別，不依賴任何定位套件，讓 [LocationService] 的邏輯
/// （距離計算、輪詢）可以在不引入 geolocator 的情況下被測試。
class LatLng {
  const LatLng({required this.latitude, required this.longitude});

  final double latitude;
  final double longitude;
}
