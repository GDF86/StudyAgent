import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './assets/main.css'
import './assets/documents.css'
import './assets/chat.css'

createApp(App).use(createPinia()).use(router).mount('#app')

import './assets/history.css'
