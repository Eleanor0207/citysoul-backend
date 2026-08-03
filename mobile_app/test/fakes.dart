import 'package:citysoul_app/services/player_api.dart';
import 'package:citysoul_app/services/secure_store.dart';

/// 記憶體版的 [SecureStore]，測試用，不碰真實的平台安全儲存區。
class FakeSecureStore implements SecureStore {
  final Map<String, String> _values = {};

  @override
  Future<String?> read(String key) async => _values[key];

  @override
  Future<void> write(String key, String value) async {
    _values[key] = value;
  }
}

/// 假的 [PlayerApi]，回傳固定值，不打真實網路請求。
class FakePlayerApi implements PlayerApi {
  FakePlayerApi({this.shouldFail = false});

  final bool shouldFail;
  int callCount = 0;
  String? lastDeviceId;

  @override
  Future<PlayerIdentity> createOrGetPlayer(String deviceId) async {
    callCount++;
    lastDeviceId = deviceId;
    if (shouldFail) {
      throw Exception('network error');
    }
    return PlayerIdentity(
      playerId: 'fake-player-id',
      deviceId: deviceId,
      accountId: null,
      sessionToken: 'fake-session-token-$callCount',
    );
  }
}
