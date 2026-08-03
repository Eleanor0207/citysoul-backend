import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';

import '../core/lat_lng.dart';

/// 取得裝置目前位置的抽象介面（seam）。
///
/// [LocationService] 的輪詢/距離判定邏輯透過這個介面依賴定位，測試用
/// 假的實作回傳固定座標，不需要真的呼叫平台定位 API（geolocator 需要
/// 平台 channel，unit test 環境沒有）。
abstract class LocationProvider {
  Future<LatLng> getCurrentPosition();
}

class GeolocatorLocationProvider implements LocationProvider {
  const GeolocatorLocationProvider();

  @override
  Future<LatLng> getCurrentPosition() async {
    await _ensurePermission();
    final position = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
    );
    return LatLng(latitude: position.latitude, longitude: position.longitude);
  }

  /// 只要求前景定位權限（whileInUse），不要求背景定位權限——
  /// 對應 CONTEXT.md「前景即時情境反應」原則。
  Future<void> _ensurePermission() async {
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      throw const LocationPermissionDeniedException();
    }
  }
}

class LocationPermissionDeniedException implements Exception {
  const LocationPermissionDeniedException();

  @override
  String toString() => '定位權限被拒絕';
}

final locationProviderProvider = Provider<LocationProvider>((ref) {
  return const GeolocatorLocationProvider();
});
