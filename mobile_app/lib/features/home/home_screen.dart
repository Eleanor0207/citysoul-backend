import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

/// 佔位首頁。真正的探索地圖畫面（S7）留待後續 ticket，這裡只證明
/// 「啟動→建立匿名身分→進入App」這條端到端路徑走得通。
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('城市靈魂'),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: () => context.go('/map'),
              child: const Text('地圖（S1 定位服務示範）'),
            ),
          ],
        ),
      ),
    );
  }
}
