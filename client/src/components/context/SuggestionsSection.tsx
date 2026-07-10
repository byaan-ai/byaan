import { useNavigate } from 'react-router-dom';
import { Sparkles, ArrowRight, Loader2 } from 'lucide-react';
import { useStore } from '../../stores/useStore';
import { useSkillSuggestions } from '../../hooks/useSkillSuggestions';
import type { SuggestionType } from '../../services/skillSuggestions';

function typeBadge(type: SuggestionType): { label: string; className: string } {
  switch (type) {
    case 'edit':
      return { label: 'Mistake', className: 'bg-red-500/10 text-red-400 border border-red-500/20' };
    case 'promotion':
      return { label: 'Promotion', className: 'bg-amber-500/10 text-amber-400 border border-amber-500/20' };
    case 'clarification':
      return { label: 'Question', className: 'bg-amber-500/10 text-amber-400 border border-amber-500/20' };
    case 'new_skill':
      return { label: 'New skill', className: 'bg-blue-500/10 text-blue-400 border border-blue-500/20' };
    case 'casebook':
      return { label: 'Casebook', className: 'bg-purple-500/10 text-purple-400 border border-purple-500/20' };
    default:
      return { label: type, className: 'bg-gray-500/10 text-gray-400 border border-gray-500/20' };
  }
}

export function SuggestionsSection() {
  const navigate = useNavigate();
  const closeSidebar = useStore((state) => state.closeSidebar);
  const { data: pending = [], isLoading } = useSkillSuggestions('pending');

  const goToReview = () => {
    closeSidebar();
    navigate('/skill-review');
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-brand-orange/10 flex items-center justify-center">
          <Sparkles className="w-5 h-5 text-brand-orange" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-white">Suggestions</h3>
          <p className="text-xs text-gray-400">
            Learnings the AI proposes from your conversations, pending your review
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-5 h-5 text-brand-orange animate-spin" />
        </div>
      ) : pending.length === 0 ? (
        <div className="text-center py-8 text-gray-500 text-sm">No pending suggestions</div>
      ) : (
        <div className="space-y-2">
          {pending.map((s) => {
            const badge = typeBadge(s.suggestion_type);
            return (
              <button
                key={s.id}
                onClick={goToReview}
                className="w-full text-left bg-[#1a1a1a] border border-gray-800 rounded-lg p-3 hover:border-gray-700 transition-colors"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-sm font-medium text-white line-clamp-2">{s.title}</span>
                  <span className={`flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium ${badge.className}`}>
                    {badge.label}
                  </span>
                </div>
                {s.skill_name && <p className="text-xs text-gray-400 mt-1 truncate">{s.skill_name}</p>}
              </button>
            );
          })}
        </div>
      )}

      <button
        onClick={goToReview}
        className="w-full flex items-center justify-center gap-2 text-sm text-brand-orange hover:underline py-2"
      >
        Open Skill Review <ArrowRight className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
