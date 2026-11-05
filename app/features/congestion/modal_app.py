# 혼잡도 관련성 선별 폴백(SemIf r2)을 Modal A10에 올리는 배포 파일. 18단계(D33).
# 배포(혼잡도 계정 _2로, 이 파일이 바뀔 때마다):
#   MODAL_TOKEN_ID=$MODAL_TOKEN_ID_2 MODAL_TOKEN_SECRET=$MODAL_TOKEN_SECRET_2 modal deploy -m app.features.congestion.modal_app
# 가중치: 기반 모델은 볼륨의 HF 캐시(/vol/hf), LoRA는 17단계 학습 결과 runs/r2/epoch-2(같은 볼륨). 지우지 않는다.
# 서버 쪽 호출은 semif.py. APP·CLS 이름은 두 파일이 같아야 한다.
# 이 파일은 서버 이미지에서 import하지 않는다(GPU 패키지는 아래 Modal 이미지에만 있다).
import modal

APP = "congestion-semif"
CLS = "SemifModel"
MODEL = "Qwen/Qwen3.5-4B"
REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"  # 학습·평가와 같은 리비전
LORA = "/vol/runs/r2/epoch-2"  # 17단계 F로 확인한 모델(온도 1, 기준값 0.2는 서버 쪽)
SEMIF = "git+https://github.com/TheoLeeCJ/SemIf.git@23cf1f39fc9534fe81437200959b6dfc7106e45a"  # MIT

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    .pip_install(
        "torch==2.10.0", "transformers==5.17.0", "accelerate==1.12.0", "safetensors==0.8.0",
        "huggingface-hub==1.31.0", "tokenizers==0.23.2", "numpy==2.2.6", "sentencepiece==0.2.1",
        "protobuf==7.36.1", "peft", "flash-linear-attention", SEMIF,
    )
    .env({"HF_HOME": "/vol/hf"})
)
volume = modal.Volume.from_name("semif-train")
app = modal.App(APP, image=image)

# 첫 컴파일을 enter에서 끝내기 위한 가짜 스팟(내용은 쓰이지 않는다).
WARMUP = [
    {
        "id": "post_1",
        "state": {"분류 기준": "교통 혼잡과 관련된 글인지 판단한다.",
                  "분류할 데이터": {"post_bundles": [{"post_id": 1, "content": "정류장 줄이 길어요"}]}, "끝": "끝"},
        "question": "post_id=1는 위 기준에 해당하는 글인가?",
        "options": [{"id": "yes", "description": "기준에 해당한다"}, {"id": "no", "description": "기준에 해당하지 않는다"}],
    }
]


# 과금은 컨테이너가 떠 있는 시간이다. 평소에는 꺼져 있다(min_containers=0).
# scaledown_window: 마지막 요청 뒤 10분 동안 요청이 없으면 꺼진다. OpenAI 장애가 이어지는 동안은 요청이 계속 와서 켜져 있다.
# max_containers=1: 동시 과금 상한. 밀린 요청은 서버의 SEMIF_WAIT를 넘기면 키워드로 넘어간다.
@app.cls(gpu="A10", volumes={"/vol": volume}, scaledown_window=600, min_containers=0, max_containers=1, timeout=600)
class SemifModel:
    @modal.enter()
    def start(self) -> None:
        from peft import PeftModel
        from semif_phase1.core import load_causal_model

        base, self.tokenizer, self.metadata = load_causal_model(MODEL, REVISION, "cuda", "bfloat16")
        self.model = PeftModel.from_pretrained(base, LORA)
        self.model.eval()
        self._score(WARMUP)  # 선형 어텐션 커널의 첫 컴파일(약 40초)을 요청 전에 끝낸다

    def _score(self, rows: list[dict]) -> dict[str, float]:
        from semif_phase1.shared import score_shared

        results, _ = score_shared(self.model, self.tokenizer, rows, self.metadata, 8192)
        return {r["id"]: r["probabilities"][r["option_ids"].index("yes")] for r in results}

    @modal.method()
    def score(self, rows: list[dict]) -> dict[str, float]:
        """스팟 하나의 SemIf 입력 줄들 → 줄 id별 관련(yes) 확률. 글 내용은 로그에 남기지 않는다."""
        return self._score(rows)
