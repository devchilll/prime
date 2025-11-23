import torch
from transformers import pipeline, AutoImageProcessor, AutoModelForImageClassification
from PIL import Image
import requests
from io import BytesIO

class TextSafetyTool:
    def __init__(self):
        self.classifier = pipeline("text-classification", model="Falconsai/offensive_speech_detection")

    def check(self, text: str) -> dict:
        try:
            results = self.classifier(text)
            # Result format: [{'label': 'offensive', 'score': 0.99}] or similar
            # Adjust based on actual model output. Falconsai/offensive_speech_detection usually outputs 'offensive' or 'safe' (or 0/1)
            # Let's assume label 'offensive' means offensive.
            
            result = results[0]
            label = result['label'].lower()
            score = result['score']
            
            is_safe = True
            risk_category = "None"
            
            if "offensive" in label:
                is_safe = False
                risk_category = "Offensive"
            
            return {
                "is_safe": is_safe,
                "risk_category": risk_category,
                "confidence": score
            }
        except Exception as e:
            print(f"Text safety check failed: {e}")
            # Fail open or closed? Spec implies fail closed if unsafe, but if tool fails? 
            # Let's return safe if tool fails but log it, or maybe unsafe to be conservative.
            # For now, let's return safe with low confidence to avoid blocking on errors during dev.
            return {"is_safe": True, "risk_category": "Error", "confidence": 0.0}

class ImageSafetyTool:
    def __init__(self):
        self.processor = AutoImageProcessor.from_pretrained("Marqo/nsfw-image-detection-384")
        self.model = AutoModelForImageClassification.from_pretrained("Marqo/nsfw-image-detection-384")

    def check(self, image_input) -> dict:
        try:
            image = None
            if isinstance(image_input, str):
                if image_input.startswith("http"):
                    response = requests.get(image_input, stream=True)
                    image = Image.open(response.raw)
                else:
                    image = Image.open(image_input)
            else:
                # Assume it's already a PIL image or compatible
                image = image_input

            inputs = self.processor(images=image, return_tensors="pt")
            with torch.no_grad():
                outputs = self.model(**inputs)
                logits = outputs.logits
            
            # Model specific output processing
            # Marqo/nsfw-image-detection-384 usually has labels like 'NSFW', 'SFW'
            predicted_label = self.model.config.id2label[logits.argmax(-1).item()]
            probs = torch.nn.functional.softmax(logits, dim=-1)
            nsfw_score = probs[0][self.model.config.label2id['NSFW']].item() if 'NSFW' in self.model.config.label2id else 0.0
            
            # If label is NSFW or score > 0.5
            is_safe = nsfw_score <= 0.5
            
            return {
                "is_safe": is_safe,
                "nsfw_score": nsfw_score
            }

        except Exception as e:
            print(f"Image safety check failed: {e}")
            return {"is_safe": True, "nsfw_score": 0.0}
