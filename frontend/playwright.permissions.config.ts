import {defineConfig} from '@playwright/test'
export default defineConfig({testDir:'./tests',testMatch:'permissions-local.spec.ts',workers:1,retries:0,timeout:45000,
 outputDir:'test-results/permissions-local',use:{baseURL:'http://127.0.0.1:5189',channel:'msedge',headless:true}})
