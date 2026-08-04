# ADR-0002：页码语义

状态：已接受（M0 基线）

证据域后续迁移必须分开保存 `physical_page_number`、`pdf_page_index` 与 `printed_page_number`；禁止以一个泛化的 `page` 字段混用三者。
