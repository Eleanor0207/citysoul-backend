import 'package:citysoul_app/core/lat_lng.dart';
import 'package:citysoul_app/services/haversine.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('distance between identical points is zero', () {
    const point = LatLng(latitude: 25.0955, longitude: 121.5186);

    expect(haversineDistanceMeters(point, point), 0);
  });

  test('matches an independently computed reference value', () {
    // 參考值用 Python 的 math.radians/sin/cos/atan2 獨立算過（同樣的標準
    // haversine公式，不同語言實作），確認不是抄同一支程式碼的巧合。
    const taipei101 = LatLng(latitude: 25.0339, longitude: 121.5645);
    const taipeiMainStation = LatLng(latitude: 25.0478, longitude: 121.5170);

    final distance = haversineDistanceMeters(taipei101, taipeiMainStation);

    expect(distance, closeTo(5028.7, 1));
  });

  test('is symmetric regardless of argument order', () {
    const a = LatLng(latitude: 25.0955, longitude: 121.5186);
    const b = LatLng(latitude: 25.0339, longitude: 121.5645);

    expect(haversineDistanceMeters(a, b), haversineDistanceMeters(b, a));
  });
}
