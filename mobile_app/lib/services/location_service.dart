import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/lat_lng.dart';
import 'location_provider.dart';

/// S1．GPS 前景定位服務。
///
/// 依《Flutter前端架構》第4.2節設計：前景開啟時每 30 秒查詢一次位置，
/// 不做背景定位、不做 geofencing（見文件已修正版本）。
///
/// 輪詢的時脈（`ticks`）是注入的，不是內部寫死的 `Timer.periodic`——這是
/// 讓 [stop] 的行為（「App 切到背景/關閉地圖畫面時停止輪詢」）可以在測試裡
/// 用一個假的 tick 來源精準驗證，不需要等待真實的 30 秒。正式環境不注入時，
/// 預設走 `Stream.periodic(pollInterval)`。
class LocationService {
  LocationService({
    required LocationProvider provider,
    this.pollInterval = const Duration(seconds: 30),
    Stream<void>? ticks,
    // ignore: prefer_initializing_formals
  })  : _provider = provider,
        _ticks = ticks ?? Stream.periodic(pollInterval);

  final LocationProvider _provider;
  final Duration pollInterval;
  final Stream<void> _ticks;

  final _positionController = StreamController<LatLng>.broadcast();
  StreamSubscription<void>? _tickSubscription;

  Stream<LatLng> get positionStream => _positionController.stream;

  bool get isPolling => _tickSubscription != null;

  /// 開始輪詢：立即查一次目前位置，之後依 [pollInterval] 週期性查詢。
  /// 對應地圖畫面前景開啟的情境。
  void start() {
    if (isPolling) return;
    _tickSubscription = _ticks.listen((_) => _poll());
    _poll();
  }

  /// 停止輪詢：對應 App 切到背景或關閉地圖畫面。停止後即使 ticks 繼續發送
  /// 事件（例如背景計時器仍在跑），也不會再查詢位置或推送新座標。
  void stop() {
    _tickSubscription?.cancel();
    _tickSubscription = null;
  }

  Future<void> _poll() async {
    final position = await _provider.getCurrentPosition();
    if (!isPolling) return; // stop() 可能在 await 期間被呼叫
    if (!_positionController.isClosed) {
      _positionController.add(position);
    }
  }

  void dispose() {
    stop();
    _positionController.close();
  }
}

final locationServiceProvider = Provider<LocationService>((ref) {
  final service = LocationService(provider: ref.watch(locationProviderProvider));
  ref.onDispose(service.dispose);
  return service;
});
