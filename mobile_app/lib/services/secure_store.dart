import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// 裝置安全儲存區的抽象介面。
///
/// 存在的原因（seam）：測試不應該碰真實的 flutter_secure_storage
/// （需要平台 channel，widget test/unit test 環境沒有），所以把讀寫
/// 行為抽成介面，測試用記憶體版本的 fake 實作即可驗證
/// [IdentityBootstrapService] 的邏輯。
abstract class SecureStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
}

class FlutterSecureStore implements SecureStore {
  const FlutterSecureStore();

  static const _storage = FlutterSecureStorage();

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _storage.write(key: key, value: value);
}

final secureStoreProvider = Provider<SecureStore>((ref) {
  return const FlutterSecureStore();
});
