def _build_video_chat_payload(
        self,
        prompt: str,
        post_id: str,
        aspect_ratio: str,
        duration_seconds: int,
        resolution: str,
        preset: str = "normal",
        image_data: str = None
    ) -> Dict[str, Any]:
        mode_map = {
            "fun": "--mode=extremely-crazy",
            "normal": "--mode=normal",
            "spicy": "--mode=extremely-spicy-or-crazy",
            "custom": "--mode=custom"
        }
        mode_flag = mode_map.get(preset, "--mode=normal")
        message = f"Follow the exact style and detail of the attached image. {prompt} {mode_flag}".strip()
        attachments = []
        if image_data:
            # Bersihkan prefix data:image/... jika ada
            if "," in image_data:
                image_data = image_data.split(",")[1]
            
            attachments.append({
                "file_name": "reference_image.jpg",
                "content_type": "image/jpeg",
                "data": image_data
            })
        return {
            "deviceEnvInfo": {
                "darkModeEnabled": False,
                "devicePixelRatio": 2,
                "screenWidth": 1920,
                "screenHeight": 1080,
                "viewportWidth": 1920,
                "viewportHeight": 980,
            },
            "disableMemory": True,
            "disableSearch": False,
            "disableSelfHarmShortCircuit": False,
            "disableTextFollowUps": False,
            "enableImageGeneration": True,
            "enableImageStreaming": True,
            "enableSideBySide": True,
            "fileAttachments": [],
            "forceConcise": False,
            "forceSideBySide": False,
            "imageAttachments": attachments,
            "imageGenerationCount": 2,
            "isAsyncChat": False,
            "isReasoning": False,
            "message": message,
            "modelMode": None,
            "modelName": "grok-3",
            "responseMetadata": {
                "requestModelDetails": {"modelId": "grok-3"},
                "modelConfigOverride": {
                    "modelMap": {
                        "videoGenModelConfig": {
                            "aspectRatio": aspect_ratio,
                            "parentPostId": post_id,
                            "resolutionName": resolution,
                            "videoLength": duration_seconds,
                        }
                    }
                }
            },
            "returnImageBytes": False,
            "returnRawGrokInXaiRequest": False,
            "sendFinalMetadata": True,
            "temporary": True,
            "toolOverrides": {"videoGen": True},
        }
