# 文件夹 → Git 纯文本打包工具

把任意文件夹（大量图片、超大文本）打包成**每个文件都小于 90KB 的纯文本**结构，
上传到有「单文件 < 100KB、只允许文本」限制的远程 Git 仓库；需要时再一键还原成原始文件夹。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `pack_folder.py` | 打包：图片转 Base64 切片，文本按 UTF-8 切片 |
| `unpack_folder.py` | 还原：拼接切片 + 解码 + SHA-256 校验 |
| `1_pack.bat` | 把文件夹**拖到它上面**即可打包（无需敲命令） |
| `2_unpack.bat` | 把打包目录**拖到它上面**即可还原 |

## 一、打包

```powershell
python pack_folder.py --src "C:\Users\jwx1416454\Desktop\20261102_famous"
```

默认输出到同级目录 `20261102_famous_packed`，也可以自己指定：

```powershell
python pack_folder.py --src "C:\...\20261102_famous" --out "C:\...\20261102_famous_packed"
```

常用参数：

- `--limit 90000`：单个文件最大字节数（默认 90000，约 87.9KB，安全低于 100KB）
- `--force`：输出目录非空时允许覆盖

## 二、上传 Git

```powershell
cd "C:\Users\jwx1416454\Desktop\20261102_famous_packed"
git init
git add .
git commit -m "add packed famous images"
git remote add origin <你的仓库地址>
git branch -M main
git push -u origin main
```

## 三、还原

```powershell
python unpack_folder.py --src "C:\...\20261102_famous_packed"
```

默认还原到 `20261102_famous_packed_restored`，也可用 `--out` 指定目标目录。
还原结束会打印「全部文件 SHA-256 一致 ✔」，说明与原文件夹逐字节相同。

## 打包后的目录结构

```
20261102_famous_packed/
├─ _pack/
│  ├─ meta.json              # 元信息：块大小、文件数、清单分卷
│  └─ manifest.0001.jsonl    # 文件清单（每行一个文件：路径/模式/大小/哈希/块列表）
└─ chunks/
   ├─ 000001.txt             # 数据块，每块 < 90KB
   ├─ 000002.txt
   └─ ...
```

## 处理规则

| 原文件 | 处理方式 |
| --- | --- |
| 图片（jpg/png/webp…） | 转 Base64 纯文本，按 90KB 切片 |
| 文本文件（txt/md/json/py…） | 按 UTF-8 切片，优先在换行处断开，Git 里可直接阅读 |
| 无扩展名文件 | 能无损当文本处理就按文本，否则走 Base64 |
| 空文件 / 空目录 | 也会记录并在还原时重建 |
| 编码 | 自动识别 UTF-8 / UTF-8-BOM / GBK / GB18030 / Big5 / UTF-16，还原时写回原编码 |

**安全性**：打包时会做「文本 → 原编码」往返校验，只要不能 100% 还原字节，就自动改用 Base64 模式。
因此任何文件都能逐字节还原，`unpack_folder.py` 还会用 SHA-256 再校验一遍。

## 实测结果（你的 20261102_famous）

- 212 个文件（202 jpg / 5 jpeg / 3 png / 1 webp / 1 txt），16.48 MB
- 打包后 393 个数据块 + 1 份清单 + meta，共 395 个文件
- 最大单文件 90000 字节
- 还原后 212 个文件 SHA-256 全部一致 ✔
