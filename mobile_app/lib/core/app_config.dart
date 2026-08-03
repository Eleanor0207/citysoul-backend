/// 後端 base URL。開發期預設指向 Android emulator 可存取本機的位址
/// （10.0.2.2 是 Android emulator 對應 host 的 loopback），可用
/// `--dart-define=API_BASE_URL=...` 覆寫（例如實機測試、iOS simulator）。
class AppConfig {
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000',
  );
}
