import {defineConfig} from '@playwright/test'
export default defineConfig({testDir:'./tests',testMatch:'phase12-local.spec.ts',workers:1,retries:0,timeout:30000,
 outputDir:'./test-results/phase12-local',use:{baseURL:'http://127.0.0.1:5189',channel:'msedge',headless:true}})
