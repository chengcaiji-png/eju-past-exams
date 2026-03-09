# EJU 留学生考试 過去問題集 (2010-2021)

日本留学生考试（EJU - Examination for Japanese University Admission for International Students）历年真题数据库。

## 📊 数据概览

### 完整数据集
- **eju_all.json** (7.3MB) - 所有EJU题目合集（2010-2021）
- **eju_flashcards.json** (141KB) - 学习闪卡
- **answer_keys.json** (31KB) - 答案解析
- **embeddings_3072.json** (37MB) - 向量嵌入数据

### 按年份分类

| 年份 | 第1回 | 第2回 |
|------|-------|-------|
| 2010 | 2010_1.json (63KB) | - |
| 2011 | 2011_1.json (104KB) | 2011_2.json (400KB) |
| 2012 | 2012_1.json (498KB) | 2012_2.json (470KB) |
| 2013 | 2013_1.json (485KB) | 2013_2.json (431KB) |
| 2014 | 2014_1.json (480KB) | 2014_2.json (370KB) |
| 2015 | 2015_1.json (380KB) | 2015_2.json (332KB) |
| 2016 | 2016_1.json (440KB) | 2016_2.json (439KB) |
| 2017 | 2017_1.json (497KB) | 2017_2.json (489KB) |
| 2018 | 2018_1.json (126KB) | 2018_2.json (310KB) |
| 2019 | 2019_1.json (362KB) | - |
| 2020 | - | 2020_2.json (261KB) |
| 2021 | 2021_1.json (328KB) | - |

总数据量：约 **50MB+**

## 🎯 考试科目

- **日本語** - 日语
- **数学** - 数学（コース1/コース2）
- **総合科目** - 综合科目（政治/经济/社会/地理/历史）

## 📁 目录结构

```
eju-past-exams/
├── README.md
├── json/
│   ├── eju_all.json           # 完整题库
│   ├── eju_flashcards.json    # 闪卡
│   ├── answer_keys.json       # 答案
│   ├── embeddings_3072.json   # 向量嵌入
│   ├── 2010_1.json ~ 2021_1.json  # 按年份分类
│   └── ...
├── images/                     # 试题图片
├── carobook/                   # CaroBook数据源
└── jasso/                      # JASSO官方数据源
```

## 🛠️ 数据处理脚本

- `download_eju.py` - 下载EJU试题
- `extract_questions.py` - 提取题目
- `extract_japanese.py` - 提取日语题
- `parse_answers.py` - 解析答案
- `convert_to_json.py` - 转换为JSON
- `build_database.py` - 构建数据库
- `ocr_extract.py` - OCR文本提取

## 📝 JSON格式示例

```json
{
  "id": "2021_1_math_01",
  "year": 2021,
  "round": 1,
  "subject": "数学",
  "question": "...",
  "choices": ["...", "...", "...", "..."],
  "answer": "A",
  "explanation": "..."
}
```

## 🔗 相关项目

- [ERE経済学検定](https://github.com/chengcaiji-png/ere-exam) - 1780题 多语言
- [宅建士試験](https://github.com/chengcaiji-png/takken-study) - 650题 中日双语
- [JLPT N1-N5](https://github.com/chengcaiji-png/jlpt-study) - 11,398题

## 📚 数据来源

- JASSO（独立行政法人日本学生支援機構）
- CaroBook EJU过去问题集
- OCR提取与人工校对

## ⚠️ 使用说明

本数据集仅供学习研究使用。请尊重版权，勿用于商业用途。

## 📄 License

MIT License

---

**创建时间：** 2026-03-09  
**数据范围：** 2010-2021年EJU考试题目
