# Notices and attribution

This repository is a demonstration, and nothing here is legal advice.

## Data

The demo uses three FY2024 annual reports (Form 10-K) of Bank of America, Morgan Stanley and Wells Fargo. They are SEC filings, labelled "Public Domain" in the dataset metadata.

The page images, page text, questions and relevance judgments come from the ViDoRe V3 Finance (English) dataset, [`vidore/vidore_v3_finance_en`](https://huggingface.co/datasets/vidore/vidore_v3_finance_en) on Hugging Face. Its annotations and judgments are licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). If you use the dataset, cite:

> António Loison, Quentin Macé, Antoine Edy, Victor Xing, Tom Balough, Gabriel Moreira, Bo Liu, Manuel Faysse, Céline Hudelot and Gautier Viaud. *ViDoRe V3: A Comprehensive Evaluation of Retrieval Augmented Generation in Complex Real-World Scenarios.* arXiv:2601.08620, 2026. https://arxiv.org/abs/2601.08620

Our changes: the page images are resized (at most 2048 px on the long side) and stored as WEBP, and the scan examples in the Parse tab are the same pages degraded to look like scans.

## Model outputs

- **Claude (Anthropic, on Amazon Bedrock):** Anthropic's Commercial Terms of Service apply. They require telling users that outputs may be inaccurate, so the Assistant shows a notice under every answer.
- **Cohere models (on Amazon SageMaker):** each AWS Marketplace listing has its own end-user licence agreement, shown when you subscribe; that agreement governs your use. The Parse tab shows Parse output for public SEC pages.

## Font

The web app uses IBM Plex Sans, © IBM Corp., under the SIL Open Font License 1.1 ([`src/ui/web/fonts/OFL.txt`](src/ui/web/fonts/OFL.txt)). The font file is unmodified.

## Trademarks and icons

Cohere, Embed, Rerank and Parse are Cohere products; Anthropic and Claude belong to Anthropic; AWS names and the AWS Architecture Icons belong to Amazon Web Services. The architecture diagram uses the AWS Architecture Icons unmodified, under [AWS's icon terms](https://aws.amazon.com/architecture/icons/).
