import React from 'react';
import { useStore } from '@/stores/useStore';
import { X, Database, FileText, Palette, Brain, Zap, BookOpen, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DatabaseUnderstandingSection } from '../DatabaseUnderstandingSection';
import { InstructionsEditor } from '../InstructionsEditor';
import { LearningsViewer } from '../LearningsViewer';
import { StyleGuidelinesEditor } from '../StyleGuidelinesEditor';
import { SkillsSection } from '../SkillsSection';
import { SuggestionsSection } from '../SuggestionsSection';
import { usePendingSuggestionCount } from '../../../hooks/useSkillSuggestions';

export const ConceptA_TabbedInterface: React.FC = () => {
  const { isSidebarOpen, closeSidebar, activeSection, setActiveSection } = useStore();
  const { data: pendingCount = 0 } = usePendingSuggestionCount();

  if (!isSidebarOpen) return null;

  const tabs = [
    { id: 'instructions' as const, label: 'Instructions', icon: FileText },
    { id: 'learnings' as const, label: 'Learnings', icon: BookOpen },
    { id: 'suggestions' as const, label: 'Suggestions', icon: Sparkles },
    { id: 'skills' as const, label: 'Skills', icon: Zap },
    { id: 'style' as const, label: 'Style Guide', icon: Palette },
    { id: 'database' as const, label: 'Datasources', icon: Database },
  ];

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40 transition-opacity duration-300"
        onClick={closeSidebar}
      />

      {/* Sidebar Panel */}
      <div className="fixed right-0 top-0 h-full w-[65%] min-w-[700px] max-w-[1200px] bg-[#0d0d0d] border-l border-gray-800 z-50 flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-orange to-brand-pink flex items-center justify-center">
              <Brain className="w-4 h-4 text-white" />
            </div>
            <h2 className="text-lg font-semibold text-white">Context</h2>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={closeSidebar}
            className="h-8 w-8 p-0 text-gray-400 hover:text-white"
          >
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 px-6 py-3 border-b border-gray-800 bg-[#1a1a1a]/50">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeSection === tab.id;

            return (
              <button
                key={tab.id}
                onClick={() => setActiveSection(tab.id)}
                className={`
                  flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all
                  ${
                    isActive
                      ? 'bg-brand-orange text-white shadow-glow-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  }
                `}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{tab.label}</span>
                {tab.id === 'suggestions' && pendingCount > 0 && (
                  <span className={`ml-1 min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-full text-[10px] font-semibold ${
                    isActive ? 'bg-white/20 text-white' : 'bg-brand-orange text-white'
                  }`}>
                    {pendingCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          <div className="p-6">
            {activeSection === 'instructions' && <InstructionsEditor />}
            {activeSection === 'learnings' && <LearningsViewer />}
            {activeSection === 'suggestions' && <SuggestionsSection />}
            {activeSection === 'skills' && <SkillsSection />}
            {activeSection === 'style' && <StyleGuidelinesEditor />}
            {activeSection === 'database' && <DatabaseUnderstandingSection />}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-800 bg-[#1a1a1a]/50">
          <p className="text-xs text-gray-500 text-center">
            Changes are saved automatically
          </p>
        </div>
      </div>

      <style>{`
        @keyframes slide-in-right {
          from {
            transform: translateX(100%);
          }
          to {
            transform: translateX(0);
          }
        }

        .animate-slide-in-right {
          animation: slide-in-right 0.3s ease-out;
        }

        .shadow-glow-orange {
          box-shadow: 0 0 20px rgba(255, 107, 53, 0.3);
        }
      `}</style>
    </>
  );
};
