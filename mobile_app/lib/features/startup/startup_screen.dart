import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../services/identity_bootstrap_service.dart';

/// 啟動畫面：背景建立匿名玩家身分，全程無登入UI（S6）。
/// 成功後導向首頁；失敗時顯示重試按鈕（不強迫玩家看到技術性錯誤訊息，
/// 但 Sprint1 範圍先給最陽春的重試機制，符合角色口吻的錯誤文案留待後續）。
class StartupScreen extends ConsumerWidget {
  const StartupScreen({super.key, required this.onReady});

  final void Function(BuildContext context) onReady;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.listen(appStartupProvider, (previous, next) {
      next.whenData((_) => onReady(context));
    });

    final startupState = ref.watch(appStartupProvider);

    return Scaffold(
      body: Center(
        child: startupState.when(
          data: (_) => const CircularProgressIndicator(),
          loading: () => const CircularProgressIndicator(),
          error: (error, stackTrace) => Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('連線失敗，請再試一次'),
              const SizedBox(height: 16),
              ElevatedButton(
                onPressed: () => ref.invalidate(appStartupProvider),
                child: const Text('重試'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
