# Naver & Kakao API Key Acquisition Guide

This guide explains how to get the Search API credentials for Naver and Kakao, and how to configure them in your `.env` file.

> [!NOTE]
> If these credentials are not set, the bot will gracefully log a warning and fall back to Google Search grounding using Gemini's built-in Google Search tool.

---

## 1. How to get Naver Search API Keys

Naver Search API provides access to Naver's Korean-centric Web documents, Blogs, and local info.

1. Go to the [Naver Developers Portal](https://developers.naver.com/).
2. Log in with your Naver account.
3. Click on **Application** in the top menu and select **Register Application** (애플리케이션 등록).
4. Enter an **Application Name** (e.g., `KoreanTeacherBot`).
5. Under **API Service**, select **Search** (검색).
6. Under **Environment Settings** (환경설정), select **Web (웹)** or **Mobile Web (모바일 웹)** and enter your application's domain/callback URL (e.g., your Cloud Run service URL `https://YOUR-BOT.run.app` or `http://localhost`).
7. Click **Register** (등록하기).
8. Go to **My Application** to see your:
   - **Client ID** (클라이언트 아이디)
   - **Client Secret** (클라이언트 시크릿)
9. Copy these values to your `.env` file as:
   ```bash
   NAVER_CLIENT_ID=your_client_id_here
   NAVER_CLIENT_SECRET=your_client_secret_here
   ```

---

## 2. How to get Kakao/Daum Search API Keys

Kakao Search API provides access to Daum's Korean web and blog results.

1. Go to the [Kakao Developers Portal](https://developers.kakao.com/).
2. Log in with your Kakao account.
3. Click on **My Application** (내 애플리케이션) in the top menu.
4. Click **Add an Application** (애플리케이션 추가하기).
5. Enter:
   - **App Name** (앱 이름, e.g., `KoreanTeacherBot`)
   - **Company Name** (회사 이름, e.g., your name or organization)
   - **Category** (카테고리)
6. Click **Save** (저장).
7. Click on your newly created application from the list.
8. In the left menu, select **App Settings** (앱 설정) > **App Keys** (앱 키).
9. Copy the **REST API Key** (REST API 키).
10. Copy this value to your `.env` file as:
    ```bash
    KAKAO_REST_API_KEY=your_rest_api_key_here
    ```

---

## 3. Local Verification

To run and test the bot locally with these new variables, update your `.env` file:

```bash
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
GEMINI_API_KEY=...
GOOGLE_CLOUD_PROJECT=...

# New variables (Optional but highly recommended)
NAVER_CLIENT_ID=your_client_id
NAVER_CLIENT_SECRET=your_client_secret
KAKAO_REST_API_KEY=your_rest_api_key
```
