import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import KnowledgeBasesView from '../views/KnowledgeBasesView.vue'
import DocumentsView from '../views/DocumentsView.vue'
import HistoryView from '../views/HistoryView.vue'
import ConversationView from '../views/ConversationView.vue'

import ChatView from '../views/ChatView.vue'
import AgentView from '../views/AgentView.vue'
import ComparisonView from '../views/ComparisonView.vue'
import PreferencesView from '../views/PreferencesView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomeView },
    { path: '/knowledge-bases', component: KnowledgeBasesView },
    { path: '/knowledge-bases/:id', component: DocumentsView },
    { path: '/history', component: HistoryView },
    { path: '/conversations/:id', component: ConversationView },
    { path: '/knowledge-bases/:id/chat', component: ChatView },
    { path: '/knowledge-bases/:id/agent', component: AgentView },
    { path: '/comparison', component: ComparisonView },
    { path: '/preferences', component: PreferencesView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
