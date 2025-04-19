# main.py
import warnings
from sklearn.exceptions import InconsistentVersionWarning
import os
import uvicorn


# 1) Silence scikit‑learn version mismatch warnings
warnings.filterwarnings("ignore", category=InconsistentVersionWarning)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import joblib
from sklearn.pipeline import Pipeline

# 2) FastAPI + CORS
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # adjust for your frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3) Load your trained pipeline
pipeline = joblib.load("logistic_pipeline_model.pkl")

# 4) Extract real feature names from the ColumnTransformer
preproc = pipeline.named_steps["preprocessing"]
feature_names = []

for name, transformer, cols in preproc.transformers_:
    # skip dropped cols or remainder
    if transformer == "drop" or name == "remainder":
        continue

    # passthrough: just re‑emit original names
    if transformer == "passthrough":
        feature_names.extend(cols)
        continue

    # if it's a Pipeline, unwrap it
    if isinstance(transformer, Pipeline):
        # find a OneHotEncoder inside, else treat as pass‑through
        ohe = None
        for step in transformer.named_steps.values():
            if hasattr(step, "categories_"):
                ohe = step
                break
        if ohe is not None:
            # manual one‑hot names
            for input_col, cats in zip(cols, ohe.categories_):
                for cat in cats:
                    feature_names.append(f"{input_col}_{cat}")
            continue
        else:
            # final step might support get_feature_names_out
            final = transformer.steps[-1][1]
            if hasattr(final, "get_feature_names_out"):
                try:
                    names = final.get_feature_names_out(cols)
                except Exception:
                    names = final.get_feature_names_out()
                feature_names.extend(names)
                continue
            else:
                feature_names.extend(cols)
                continue

    # if it's a bare OneHotEncoder
    if hasattr(transformer, "categories_"):
        for input_col, cats in zip(cols, transformer.categories_):
            for cat in cats:
                feature_names.append(f"{input_col}_{cat}")
        continue

    # if transformer can emit names
    if hasattr(transformer, "get_feature_names_out"):
        try:
            names = transformer.get_feature_names_out(cols)
        except Exception:
            names = transformer.get_feature_names_out()
        feature_names.extend(names)
        continue

    # fallback: assume cols passed unchanged
    feature_names.extend(cols)

# 5) Define Pydantic schema
class InputData(BaseModel):
    Albuminuria: int
    WaistCirc: float
    UricAcid: float
    BloodGlucose: float
    HDL: float
    Triglycerides: float
    Age: int
    Sex: str
    Marital: str
    UrAlbCr: float
    Race: str

# 6) Prediction endpoint
@app.post("/predict")
def predict(data: InputData):
    # a) DataFrame from JSON
    df = pd.DataFrame([data.dict()])

    # b) predict + proba
    pred = pipeline.predict(df)[0]
    proba = pipeline.predict_proba(df)[0][1]

    # c) transform & grab coefs
    X_proc = preproc.transform(df)
    coefs = pipeline.named_steps["classifier"].coef_[0]

    # d) compute contributions
    contribs = X_proc[0] * coefs

    # e) assemble top‑5
    contrib_df = pd.DataFrame({
        "feature": feature_names,
        "contribution": contribs
    })
    contrib_df["abs_val"] = contrib_df.contribution.abs()
    top5 = (
        contrib_df
        .nlargest(5, "abs_val")
        .loc[:, ["feature", "contribution"]]
        .to_dict(orient="records")
    )

    # f) return JSON
    return {
        "prediction": int(pred),
        "probability": float(proba),
        "contributions": top5
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
