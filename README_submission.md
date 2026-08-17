# Lab 17 - Bài nộp

- Họ tên: Tạ Minh Đức
- MSSV: 2A202601497
- Impl: `src/memory_student.py`, report: `reports/benchmark.md`

## 1. Short-term memory và compaction (Pha A)

Compaction đẩy constraint `REVIEW-DEADLINE-1600` ("Friday", "16:00") từ raw turn sang `<DURABLE_NOTES>`, ưu tiên state/decision/TODO. Hạ `max_recent_messages` 6 -> 4: còn 4 recent turn + 12 compaction, deadline vẫn trong render dù raw turn đầu đã evict.

Buffer không đủ: token tăng tuyến tính (217/878/2916 ở 15/61/201 turn, `sliding` chặn ~735) và không phân biệt constraint với filler — hard window 4 turn mất deadline, `sliding` giữ. E01, E10 PASS.

## 2. Ba câu bắt buộc

1. **Layer quan trọng nhất: `long_term`** — 4/11 case (E02, E03, E08, E09) cộng nửa E07. Case **E03** (open loop "16:00"): Context Block và `scope="edges"` chỉ trả paraphrase "has a to-do item to complete the benchmark report", mất literal `16:00`; phải backfill `scope="episodes"` mới PASS. Reference impl không backfill nên FAIL E03 (10/11).
2. **Trade-off**: Redis + Qdrant rẻ, deterministic (profile hash TTL 7776000s, top score 0.475 vs 0.047 noise) nhưng chỉ là KV + vector: không tự invalidate fact khi preference đổi, không provenance/`valid_at`, phải tự viết ranking + assemble. Zep cho Context Block user-scoped, `invalid_at` (E08), episode verbatim; đổi lại latency 1.6–3.3s/case và ranking không deterministic — đúng chỗ E03 flake.
3. **Guardrail poisoning**: consent gate + PII minimization trước khi ghi durable; schema `control_plane/MEMORY.md` bắt source/timestamp/confidence/validity, compiled KB page (`wiki-payment-retry`) mang `provenance`/`source_ids`/`contradictions` nên fact không nguồn bị loại; policy heartbeat "Never create a high-impact task or preference change without policy/human review"; conflict theo recency + scope, không ghi đè toàn cục.

## 3. Phân tích benchmark

1. Hit rate: student 100% mọi layer (11/11). No-memory: long_term 0/4, episodic 0/2, semantic 0/2, mixed 0/1, short_term 2/3.
2. Nhiều token nhất: **E02** 1544 token retrieved (Context Block + facts), trên E03/E08 1533.
3. **E07 = long_term + semantic**; evidence `Python` (long_term) và `Idempotency-Key` (PAYMENT-RULE-3, semantic); long_term raw 1544 trim còn 324 theo limit 320.
4. Reduction 14.2% với hit 100%; no-memory reduction 81.8% nhưng hit 18.2% — reduction chỉ đo lượng context, không đo đúng evidence.

## 4. Recency và compaction

- E08: `scope="edges"` trả `BLUEBIRD-42 uses TypeScript/NestJS` còn hiệu lực, fact cũ `prioritizes Python` bị đóng `invalid_at=2026-08-01T09:00:20Z` — superseded, không xoá, còn trace provenance; Python vẫn đúng cho scope ORCHID-27, nên conflict theo recency **và** scope.
- E10: xem mục 1.
