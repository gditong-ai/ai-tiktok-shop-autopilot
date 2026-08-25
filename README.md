# AI TikTok Shop Autopilot — GitHub + Render

ระบบ starter สำหรับทำงานอัตโนมัติ: สินค้า → Gemini วิเคราะห์/สร้างสคริปต์ → สร้างวิดีโอ 9:16 → คิวโพสต์ → เชื่อม TikTok API ที่ได้รับอนุญาต

## Deploy ด้วย GitHub + Render

1. สร้าง GitHub repository ใหม่ เช่น `ai-tiktok-shop-autopilot`.
2. อัปโหลดไฟล์/โฟลเดอร์ทั้งหมดใน repository นี้ขึ้น GitHub.
3. ที่ Render เลือก **New → Blueprint** แล้วเชื่อม GitHub repository.
4. Render จะอ่าน `render.yaml` และสร้าง API + Dashboard + PostgreSQL ตาม Blueprint. Render รองรับ Blueprint ผ่าน `render.yaml` ที่อยู่ root ของ repository. 
5. ตอนสร้างครั้งแรก Render จะถามค่า secret ที่มี `sync: false`; ใส่ Gemini API Key ของคุณตรงนั้นเท่านั้น.
6. Deploy.

## URLs

- API: `https://<your-api-service>.onrender.com`
- API docs: `https://<your-api-service>.onrender.com/docs`
- Health: `https://<your-api-service>.onrender.com/health`
- Dashboard: `https://<your-dashboard>.onrender.com`

## Gemini

ห้าม commit API key ลง GitHub. ใช้ Render Environment Variable `GEMINI_API_KEY`.

## TikTok

ระบบนี้ไม่ทำ scraping, session theft หรือ bypass. การโพสต์อัตโนมัติต้องใช้ TikTok Content Posting API และสิทธิ์ OAuth ที่ได้รับอนุญาต. Direct Post ต้องใช้ `video.publish` และ TikTok ระบุว่าคลายข้อจำกัด public posting ต้องผ่าน audit ของ client; unaudited clients จะถูกจำกัดเป็น private. 

## สถานะ v1

มีแล้ว:
- PostgreSQL product/trend/video/queue schema
- Gemini trend scoring
- Gemini script generation
- FFmpeg 9:16 pipeline test
- Product dashboard
- Render Blueprint
- Secret-safe environment configuration

ต้องต่อเพิ่มก่อน production:
- TikTok OAuth + Content Posting API adapter
- TikTok Shop product mapping ตามสิทธิ์บัญชี
- Product-image upload/storage
- TTS + subtitle pipeline
- Approved analytics ingestion
- Rate limits/retry/kill switch

## หมายเหตุเรื่องฟรี

Render รองรับ free web/static services และ free Postgres ตามชนิดทรัพยากรที่รองรับ แต่ resource/โควตาและข้อกำหนดของผู้ให้บริการอาจเปลี่ยนได้. ระบบนี้จึงไม่รับประกันว่า AI generation หรือการประมวลผลวิดีโอจะฟรีไม่จำกัด.
