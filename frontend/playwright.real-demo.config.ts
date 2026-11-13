import { defineConfig } from '@playwright/test'
export default defineConfig({testDir:'./tests',testMatch:'real-demo.spec.ts',workers:1,retries:0,timeout:180000,
 outputDir:'./test-results/phase8-real',use:{baseURL:'http://127.0.0.1:5189',channel:'msedge',headless:true},
 webServer:{command:'npm run dev -- --port 5189',url:'http://127.0.0.1:5189',reuseExistingServer:false}})
