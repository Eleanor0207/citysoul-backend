import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import 'api_client.dart';
import 'player_api.dart';
import 'secure_store.dart';

const deviceIdStorageKey = 'device_id';

/// S6．匿名玩家身分系統（App 端）。
///
/// App 啟動時呼叫一次：裝置端如果沒有 `device_id` 就產生一組隨機值存起來
/// （對應 CONTEXT.md「匿名玩家」定義），然後呼叫後端 `/players` 建立/取得
/// 玩家身分，並把回傳的 `session_token` 存進安全儲存區。全程不出現任何
/// 登入/註冊UI，玩家無感知。
class IdentityBootstrapService {
  IdentityBootstrapService({
    required this.secureStore,
    required this.playerApi,
    Uuid? uuid,
  }) : _uuid = uuid ?? const Uuid();

  final SecureStore secureStore;
  final PlayerApi playerApi;
  final Uuid _uuid;

  Future<PlayerIdentity> bootstrap() async {
    var deviceId = await secureStore.read(deviceIdStorageKey);
    if (deviceId == null) {
      deviceId = _uuid.v4();
      await secureStore.write(deviceIdStorageKey, deviceId);
    }

    final identity = await playerApi.createOrGetPlayer(deviceId);
    await secureStore.write(sessionTokenStorageKey, identity.sessionToken);
    return identity;
  }
}

final identityBootstrapServiceProvider = Provider<IdentityBootstrapService>((
  ref,
) {
  return IdentityBootstrapService(
    secureStore: ref.watch(secureStoreProvider),
    playerApi: ref.watch(playerApiProvider),
  );
});

/// App 啟動時觸發一次的身分建立流程，StartupScreen 監聽這個 provider。
final appStartupProvider = FutureProvider<PlayerIdentity>((ref) {
  return ref.watch(identityBootstrapServiceProvider).bootstrap();
});
