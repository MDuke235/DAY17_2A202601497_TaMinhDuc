# Lab 17 - Bài nộp

- Họ tên: Tạ Minh Đức
- MSSV: 2A202601497
- Impl: `src/memory_student.py`, report: `reports/benchmark.md`

## 1. Short-term memory và compaction (Pha A)

Compaction chuyển constraint `REVIEW-DEADLINE-1600` ("Friday", "16:00") từ raw turn sang `<DURABLE_NOTES>`, ưu tiên state/decision/TODO thay vì tóm tắt đều. Nên khi hạ `max_recent_messages` từ 6 xuống 4, `sliding` chỉ còn 4 recent turn và 12 compaction mà deadline vẫn còn trong render, dù raw turn đầu tiên đã bị evict.

Buffer không đủ: token tăng tuyến tính theo số turn (217 -> 878 -> 2916 token ở 15/61/201 turn, `sliding` chặn ở ~735), và buffer không phân biệt constraint với filler. Mô phỏng hard window 4 turn: buffer mất `REVIEW-DEADLINE-1600` vì turn cũ nhất bị evict trước, `sliding` vẫn giữ nhờ durable note. E01 và E10 PASS trong `reports/benchmark.md`.

## 2. Ba câu bắt buộc

1. Layer quan trọng nhất trong bộ test này và case cụ thể: _(điền sau khi chạy benchmark student)_
2. Trade-off Context Block / Zep vs tự build Redis + Qdrant: _(điền sau mini-drill)_
3. Guardrail chống memory poisoning: _(điền sau mini-drill)_

## 3. Phân tích benchmark

1. Layer có hit rate thấp nhất: _(điền)_
2. Query retrieve nhiều token nhất: _(điền)_
3. E07 cần kết hợp memory nào, evidence bắt buộc: _(điền)_
4. Token reduction so với full source context, và vì sao no-memory có reduction cao nhưng hit rate thấp: _(điền)_

## 4. Recency và compaction

- E08 recency: sau session cập nhật, `graph.search(scope="edges")` trả về `The BLUEBIRD-42 uses TypeScript/NestJS (2026-08-05 08:00:20)` còn hiệu lực, trong khi fact cũ `Minh Nguyen prioritizes Python` bị đóng bằng `invalid_at=2026-08-01T09:00:20Z`. Fact cũ không bị xoá mà chỉ bị đánh dấu superseded, nên vẫn trace được provenance. Preference Python vẫn đúng cho scope khác là demo cá nhân ORCHID-27, tức conflict giải theo recency **và** scope chứ không phải ghi đè toàn cục.
- E10 compaction: xem mục 1.
