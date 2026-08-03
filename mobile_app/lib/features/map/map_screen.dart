import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/lat_lng.dart';
import '../../services/haversine.dart';
import '../../services/location_service.dart';

/// 天文館垂直切片的召喚點座標（對應後端 seed data，見 app/db/seed.py）。
/// 純粹作為這個佔位畫面的示範地標，正式的探索地圖畫面（S7）會從
/// `GET /api/v1/spirits/{placeId}` 取得，不是寫死在前端。
const _demoLandmark = LatLng(latitude: 25.0955, longitude: 121.5186);

/// S1 的可demo畫面：顯示目前裝置座標與示範地標的距離，證明
/// LocationService 的前景輪詢運作正常。真正的探索地圖畫面（S7，含三段式
/// 標記、OSM圖磚）是後續 ticket 的範圍，這裡只驗證定位服務本身。
class MapScreen extends ConsumerStatefulWidget {
  const MapScreen({super.key});

  @override
  ConsumerState<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends ConsumerState<MapScreen> {
  @override
  void initState() {
    super.initState();
    ref.read(locationServiceProvider).start();
  }

  @override
  void dispose() {
    ref.read(locationServiceProvider).stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final locationService = ref.watch(locationServiceProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('地圖（S1 定位服務示範）')),
      body: Center(
        child: StreamBuilder<LatLng>(
          stream: locationService.positionStream,
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return const Text('無法取得目前位置');
            }
            if (!snapshot.hasData) {
              return const CircularProgressIndicator();
            }

            final position = snapshot.data!;
            final distance = haversineDistanceMeters(position, _demoLandmark);

            return Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  '目前座標：${position.latitude.toStringAsFixed(5)}, '
                  '${position.longitude.toStringAsFixed(5)}',
                ),
                const SizedBox(height: 8),
                Text('與天文館距離：約 ${distance.toStringAsFixed(0)} 公尺'),
              ],
            );
          },
        ),
      ),
    );
  }
}
