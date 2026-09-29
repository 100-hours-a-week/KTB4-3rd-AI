# 이체 영수증

`POST /receipt`는 영수증 사진에서 송금 금액, 이체 완료 정보, 보낸 사람, 보낸 시각을 읽어 돌려준다.

## 파일

`router.py`는 `POST /receipt`를 받는다. 요청 본문을 검사한 뒤 `main.run`을 부른다.

`main.py`의 `run`은 요청 하나의 순서를 정한다. 이미지를 한 번 받는다. `IMAGE_DEVICE`가 `off`이면 `model_api_run`만 부른다. 그 외에는 `model_pipe_run`을 먼저 돌리고, 결과가 없거나 로컬 모델이 실패하면 `model_api_run`을 부른다. 이 서버에서 모델을 돌릴 때는 로컬 모델 구간을 한 요청씩 처리한다.

`pipeline.py`의 `model_pipe_run`은 OCR로 글자를 읽고 openjev로 금액, 시각, 이체 완료, 보낸 사람을 고른다. 네 필드가 모두 pass일 때만 값을 돌려준다. 한 필드라도 pass가 아니면 값을 만들지 않는다. `model_api_run`은 luna API로 같은 사진을 읽어 네 필드를 만든다.

`image.py`는 사진 URL을 받아 온다. 파일이 비었거나 5MB를 넘거나 JPEG, PNG가 아니면 오류를 낸다.

`models.py`는 요청 필드 `image_URL`, `ride_channel_id`와 응답 필드 `success`, `amount_cost`, `transfer_info`, `sender_name`, `send_time`을 정의한다.
