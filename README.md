# 簡繁轉換與章節整理工具

TXT / EPUB（簡體或繁體）→ 簡繁轉換 → 自動切章 → 輸出單一 TXT、多檔 TXT 或 EPUB（含封面與目錄）。

## 安裝與啟動
```
pip install -r requirements.txt
python main.py
```
Windows / macOS 的 Python 內建 tkinter；Debian / Ubuntu 需另外 `sudo apt install python3-tk`。

## 使用流程
1. 拖放或「選取檔案」（可多選）。選到檔案會自動分析章節。
2. 在左側調整：輸出文字（繁體三種詞彙模式 / 簡體）、輸出格式、切分規則、封面。
3. 右側預覽樹可手動調整：合併、拆分、改名、刪除、勾選是否輸出（`Ctrl+Z` 復原）。
4. 改了「切分」設定後按「重新分析章節」；其他設定不需重新分析。
5. 「轉換輸出」處理目前這本；「批次轉換全部檔案」依目前設定處理清單內所有檔案（自動切分，不套用手動編輯）。

## 切分規則
- **預設規則**：第X章/回/節/卷/集/部/篇、Chapter N、序章、楔子、番外、後記…（簡繁都涵蓋）
- **自訂句首**：一行一個，行首相符就當章節；輸入簡體也能比對繁體書，反之亦然。
- **自訂 regex**：進階用，一行一個。
- 標題行須在 40 字內，避免把正文裡提到「第三章」的句子當標題。
- EPUB 輸入預設沿用原書章節；勾「重新切分」會捨棄原書章節內的圖片與版面。

## 封面（僅 EPUB 輸出）
沿用原封面 → 使用者圖片 → 自動產生（保底，永遠有封面）。
自動封面需要中文字型：優先使用 `assets/fonts/` 內的字型，其次系統字型，也可在封面分頁自選。

## 測試
```
python tests/test_core.py
python tests/test_epub.py
python tests/test_cover.py
python tests/test_logic.py
xvfb-run -a python tests/test_gui_smoke.py     # GUI；Windows/macOS 直接 python 執行即可
```

## 打包成 exe（Windows，未經實機驗證）
```
pip install pyinstaller
pyinstaller --noconsole --onefile --name BookConverter ^
  --collect-data opencc --collect-all tkinterdnd2 ^
  --add-data "assets;assets" main.py
```
- macOS / Linux 的 `--add-data` 分隔符是 `:`（`assets:assets`）。
- 若啟動後簡繁轉換報找不到字典，請確認 `--collect-data opencc` 有生效。
- 想完全不依賴系統字型，把思源黑體放進 `assets/fonts/` 再打包。

## 目錄
```
core/   不依賴 GUI：編碼偵測、簡繁轉換、章節切分、EPUB 讀寫、TXT 輸出、封面、章節編輯
gui/    Tkinter 介面；jobs / worker / settings 不依賴 Tk，可獨立測試
tests/  單元測試與 GUI 冒煙測試
```
