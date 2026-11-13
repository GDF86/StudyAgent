import {defineConfig} from '@playwright/test'
export default defineConfig({testDir:'./tests',testMatch:'phase11-real.spec.ts',workers:1,retries:0,timeout:240000,
 outputDir:'./test-results/phase11-real',use:{baseURL:'http://127.0.0.1:5189',channel:'msedge',headless:true}})
