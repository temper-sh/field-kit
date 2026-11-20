# Gemma 26B on M5 Pro / 64 GiB — Google versus Unsloth

Run one new Field Kit study named `gemma26-google-vs-unsloth-62k`. It
compares two QAT weight exports on one lookup workload. Start from this
folder's prepared inputs. Previously returned studies are complete.

The question is whether Google's official Q4_0 export retrieves the middle
record that Unsloth's UD-Q4_K_XL missed. In the original 62,194-token cold
input, Unsloth returned record 01306 when asked for 01300. It repeated that
error after an append and after an early edit. This comparison also measures
the latency and memory cost of each export on this Mac.

## Prepare and run

1. From the Field Kit root, use the installed Temper and the existing weights
   cache. Preview one study with this brief and **both** [Unsloth](unsloth-q4.lock.json)
   and [Google](google-q4_0.lock.json) execution locks. The locks pin the
   weights, llama.cpp b11756, plain decoding, F16 K and V, one slot and a
   131,072-token window. Review any missing material in the preview. The Google
   GGUF is 14,439,363,584 bytes and the Unsloth GGUF is 14,249,047,104 bytes.
   Size the download allowance from missing files and software. Ground the time
   limits in the original cold / append / edit times of 64.6 / 1.8 / 65.0 s,
   with room for download, preparation, cleanup and the conditional check below.
   Use the [study initialization instructions](../docs/TOOLS.md#start-a-study)
   to show the preview and obtain machine-side approval.
2. Run the prepared queue from the Field Kit root:

   ```sh
   ./field-kit study queue gemma26-google-vs-unsloth-62k --plan studies/comparison.json
   ```

   Each export starts cold. Both use the [same frozen workload](google-vs-unsloth-62k.json)
   from `gemma26-rerun-20261118/runs/20261119T011103607092Z`. Its 2,600
   records, temperature 0, seed 17, thinking off and 2,048-token output
   allowance reproduce the original diagnostic. The append carries each
   export's actual answer. The early edit replaces the first record's value.
   Check the rendered cold prompt for both embedded templates before
   attributing a difference to weights.
3. Compare each returned value with its step's expected array. Record which
   record supplied any wrong value. Score bare-JSON formatting separately
   from semantic correctness after removing an outer Markdown fence. Preserve
   a truncated answer as incomplete. For each step report actual input and
   cached tokens and service seconds. For each export report loaded and peak
   system wired growth in GiB and swap growth. Sampling can miss short peaks
   and wired growth includes other applications.
4. If Google improves retrieval, check both exports on the same fresh
   thinking-on source-and-correction task within this study. Freeze its source,
   expected facts, correction, and common reasoning/output policy before either
   answer. Otherwise finish after the lookup pair. Record what each export
   got right, the cost, and whether the evidence favors either for this work.
5. Close this study, restore the layout and return **one results ZIP** with
   both attempts and the conclusion. [Field Kit agent guide](../docs/ASSISTANT.md)
