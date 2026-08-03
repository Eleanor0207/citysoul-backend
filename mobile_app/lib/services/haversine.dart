import 'dart:math';

import '../core/lat_lng.dart';

const double _earthRadiusMeters = 6371000;

/// S2 在場驗證／`/sense` 感應驗證都用同一條公式（見 SDD 第7.1/7.2節）。
/// 這裡只算距離，門檻比較（`summon_radius_m`/`sense_radius_m`）留給
/// 呼叫端的畫面邏輯，不在這支函式裡做。
double haversineDistanceMeters(LatLng a, LatLng b) {
  final dLat = _degToRad(b.latitude - a.latitude);
  final dLon = _degToRad(b.longitude - a.longitude);

  final lat1 = _degToRad(a.latitude);
  final lat2 = _degToRad(b.latitude);

  final h = sin(dLat / 2) * sin(dLat / 2) +
      sin(dLon / 2) * sin(dLon / 2) * cos(lat1) * cos(lat2);
  final c = 2 * atan2(sqrt(h), sqrt(1 - h));

  return _earthRadiusMeters * c;
}

double _degToRad(double deg) => deg * pi / 180;
