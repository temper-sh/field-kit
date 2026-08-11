I run a local LLM stack on a MacBook Air M5 32GB (Apple Silicon, fanless,
153GB/s memory bandwidth): llama-swap routing to Rapid-MLX (MLX models) and
llama-server/llama.cpp (GGUF), used by the Pi coding agent and OpenGSD over
the OpenAI API on localhost:8080. Models are declared in a YAML manifest
(id, engine, HF repo, flags, patches) and all configs are generated from it.

I want to add: <MODEL NAME>. Research with current web sources:

1. Best variant for my setup: the exact HuggingFace repo ID (verify it exists
   and check its last-updated date). Prefer MLX 4-bit for Apple Silicon;
   prefer fixed/UD builds (unsloth, froggeric) over stock conversions when
   they exist. If there is no good MLX build, give the best GGUF (repo:quant)
   for llama.cpp instead.
2. Memory fit: weights size at 4-bit, realistic working set on 32GB
   (weights + KV cache), and the context cap I should set.
3. Known issues on MLX/llama.cpp serving: chat-template bugs, tool-calling
   parser requirements, reasoning-channel quirks (answers landing in
   reasoning_content instead of content), multi-turn agentic failures
   (empty responses once tool history accumulates). State explicitly whether
   the froggeric Qwen fixed template (or another patch) is needed, or
   whether a pre-fixed build exists.
4. Recommended serve flags for agentic use: tool-call parser name, thinking
   on/off, template kwargs, anything engine-specific.
5. Output a ready manifest entry:
   id, display_name, engine (rapid-mlx | llama-server), repo, patches [],
   flags "", ttl, group (pinned | utilities | heavy), kind (only if not a
   chat model), and pi extras (e.g. reasoning: true).
6. A one-request smoke test: a curl to /v1/chat/completions including a
   tools[] array AND simulated multi-turn tool history (assistant tool_calls
   + tool results, including one error result) that would expose this model
   family's known failure modes.

Dense vs MoE matters: flag expected generation speed on 153GB/s bandwidth
(MoE active-params vs dense full-weight reads). Cite sources for any claimed
bug or fix.
