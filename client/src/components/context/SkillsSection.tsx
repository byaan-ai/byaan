import { useEffect, useState, useMemo } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { useStore } from '@/stores/useStore';
import { CheckCircle, ExternalLink, Github, Globe, Key, Shield, Users, Zap, Loader2, Search, Plus, Edit2, Trash2 } from 'lucide-react';
import type { SkillScope, SkillStatus, CustomSkill, CreateCustomSkillData, CredentialField } from '@/stores/slices/contextSlice';
import { useScopes } from '@/hooks/useScopes';
import { WriteSkillModal } from './WriteSkillModal';
import { ConfirmationModal } from '@/components/ConfirmationModal';

type CategoryTab = 'byaan' | 'custom' | 'whitelisted';

function isFieldVisible(
  field: CredentialField,
  fields: CredentialField[],
  credentials: Record<string, string>
): boolean {
  const dep = field.depends_on;
  if (!dep?.key) return true;
  const depField = fields.find(f => f.key === dep.key);
  const current = credentials[dep.key]?.trim() || depField?.default || '';
  return current === dep.value;
}

export function SkillsSection() {
  const {
    skills,
    isLoadingSkills,
    loadSkills,
    saveSkillCredentials,
    deleteSkillCredentials,
    shareSkillWithTeam,
    toggleSkillDomain,
    customSkills,
    isLoadingCustomSkills,
    loadCustomSkills,
    pendingSkillId,
    setPendingSkillId,
    createCustomSkill,
    updateCustomSkill,
    deleteCustomSkill,
    shareCustomSkill,
    unshareCustomSkill,
    toggleCustomSkillDomain,
    user,
  } = useStore();
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [isSharing, setIsSharing] = useState<string | null>(null);
  const [isUnsharing, setIsUnsharing] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState<CategoryTab>('byaan');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingCustomSkill, setEditingCustomSkill] = useState<CustomSkill | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const { features } = useScopes();
  const teamSharingEnabled = features.team_sharing_enabled;

  const currentUserId = user?.id;

  useEffect(() => {
    loadSkills();
    loadCustomSkills();
  }, [loadSkills, loadCustomSkills]);

  useEffect(() => {
    if (!pendingSkillId || isLoadingCustomSkills) return;
    const skill = customSkills.find((s) => s.id === pendingSkillId);
    if (skill) {
      setActiveTab('custom');
      setEditingCustomSkill(skill);
      setIsModalOpen(true);
      setPendingSkillId(null);
    }
  }, [pendingSkillId, customSkills, isLoadingCustomSkills, setPendingSkillId]);

  const filteredSkills = useMemo(() => {
    if (activeTab !== 'byaan') return [];

    let result = skills;

    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(s =>
        s.display_name.toLowerCase().includes(query) ||
        s.description?.toLowerCase().includes(query)
      );
    }

    return result;
  }, [skills, searchQuery, activeTab]);

  const filteredCustomSkills = useMemo(() => {
    if (activeTab !== 'custom') return [];

    let result = customSkills;

    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(s =>
        s.name.toLowerCase().includes(query) ||
        s.description?.toLowerCase().includes(query)
      );
    }

    return result;
  }, [customSkills, searchQuery, activeTab]);

  type DomainEntry =
    | { source: 'byaan'; skill: SkillStatus }
    | { source: 'custom'; skill: CustomSkill };

  const domainEntries = useMemo<DomainEntry[]>(() => {
    const entries: DomainEntry[] = [];
    for (const s of skills) {
      if (s.domain && s.is_configured) {
        entries.push({ source: 'byaan', skill: s });
      }
    }
    for (const s of customSkills) {
      if (s.can_execute_api && s.api_domain) {
        entries.push({ source: 'custom', skill: s });
      }
    }
    return entries;
  }, [skills, customSkills]);

  async function handleSave(skillName: string, scope: SkillScope) {
    const skill = skills.find(s => s.skill_name === skillName);
    if (!skill) return;

    const trimmedCredentials: Record<string, string> = {};
    for (const field of skill.credential_fields) {
      if (!isFieldVisible(field, skill.credential_fields, credentials)) continue;
      const value = credentials[field.key]?.trim() || (field.type === 'select' ? field.default : '') || '';
      if (!value && !field.optional && !skill.is_configured) return;
      if (value) trimmedCredentials[field.key] = value;
    }

    setIsSaving(true);
    try {
      await saveSkillCredentials(skillName, trimmedCredentials, scope);
      setEditingSkill(null);
      setCredentials({});
    } finally {
      setIsSaving(false);
    }
  }

  async function handleDisconnect(skillName: string, scope: SkillScope) {
    const scopeLabel = scope === 'org' ? 'team' : 'personal';
    if (!window.confirm(`Remove your ${scopeLabel} ${skillName} credentials?`)) return;
    await deleteSkillCredentials(skillName, scope);
  }

  async function handleShare(skillName: string) {
    setIsSharing(skillName);
    try {
      await shareSkillWithTeam(skillName);
    } finally {
      setIsSharing(null);
    }
  }

  async function handleUnshare(skillName: string) {
    setIsUnsharing(skillName);
    try {
      await deleteSkillCredentials(skillName, 'org');
    } finally {
      setIsUnsharing(null);
    }
  }

  function handleCancel() {
    setEditingSkill(null);
    setCredentials({});
  }

  async function handleSaveCustomSkill(data: CreateCustomSkillData) {
    if (editingCustomSkill) {
      await updateCustomSkill(editingCustomSkill.id, data);
    } else {
      await createCustomSkill(data);
    }
    setEditingCustomSkill(null);
  }

  async function handleDeleteCustomSkill(id: string, name: string) {
    setDeleteTarget({ id, name });
  }

  async function handleDeleteConfirm() {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await deleteCustomSkill(deleteTarget.id);
    } catch {
      // error handled in store
    } finally {
      setIsDeleting(false);
      setDeleteTarget(null);
    }
  }

  async function handleShareCustomSkill(id: string) {
    setIsSharing(id);
    try {
      await shareCustomSkill(id);
    } finally {
      setIsSharing(null);
    }
  }

  async function handleUnshareCustomSkill(id: string) {
    setIsUnsharing(id);
    try {
      await unshareCustomSkill(id);
    } finally {
      setIsUnsharing(null);
    }
  }

  if (isLoadingSkills || isLoadingCustomSkills) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-5 h-5 text-brand-orange animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-brand-orange/10 flex items-center justify-center">
          <Zap className="w-5 h-5 text-brand-orange" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-white">Skills</h3>
          <p className="text-xs text-gray-400">
            Connect services to extend AI capabilities
          </p>
        </div>
      </div>

      {/* Search Input */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
        <Input
          type="text"
          placeholder="Search skills..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="pl-9 bg-[#1a1a1a] border-gray-800 text-sm h-9"
        />
      </div>

      {/* Category Sub-tabs */}
      <div className="flex gap-2">
        <button
          onClick={() => setActiveTab('byaan')}
          className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
            activeTab === 'byaan'
              ? 'bg-brand-orange text-white'
              : 'bg-[#1a1a1a] text-gray-400 hover:text-white border border-gray-800'
          }`}
        >
          Byaan Skills
        </button>
        <button
          onClick={() => setActiveTab('custom')}
          className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
            activeTab === 'custom'
              ? 'bg-brand-orange text-white'
              : 'bg-[#1a1a1a] text-gray-400 hover:text-white border border-gray-800'
          }`}
        >
          Custom Skills
        </button>
        <button
          onClick={() => setActiveTab('whitelisted')}
          className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
            activeTab === 'whitelisted'
              ? 'bg-brand-orange text-white'
              : 'bg-[#1a1a1a] text-gray-400 hover:text-white border border-gray-800'
          }`}
        >
          Whitelisted Domains
        </button>
      </div>

      {/* Byaan Skills List */}
      {activeTab === 'byaan' && (
        <>
          <div className="space-y-3">
            {filteredSkills.map((skill) => {
              const hasPersonal = skill.scopes_configured?.includes('user') && skill.user_scope_created_by === currentUserId;
              const hasOrg = skill.scopes_configured?.includes('org');
              const isOrgOwner = skill.org_scope_created_by === currentUserId;

              return (
                <SkillCard
                  key={skill.skill_name}
                  skill={skill}
                  hasPersonal={hasPersonal}
                  hasOrg={hasOrg}
                  isOrgOwner={isOrgOwner}
                  isEditing={editingSkill === skill.skill_name}
                  credentials={credentials}
                  isSaving={isSaving}
                  isSharing={isSharing === skill.skill_name}
                  isUnsharing={isUnsharing === skill.skill_name}
                  teamSharingEnabled={teamSharingEnabled}
                  onEdit={() => {
                    setCredentials({});
                    setEditingSkill(skill.skill_name);
                  }}
                  onCredentialChange={(key, value) => setCredentials(prev => ({ ...prev, [key]: value }))}
                  onSave={() => handleSave(skill.skill_name, 'user')}
                  onCancel={handleCancel}
                  onDisconnect={() => handleDisconnect(skill.skill_name, 'user')}
                  onShare={() => handleShare(skill.skill_name)}
                  onUnshare={() => handleUnshare(skill.skill_name)}
                />
              );
            })}
          </div>

          {filteredSkills.length === 0 && (
            <div className="text-center py-8 text-gray-500 text-sm">
              {searchQuery ? 'No skills match your search' : 'No skills available'}
            </div>
          )}
        </>
      )}

      {/* Custom Skills */}
      {activeTab === 'custom' && (
        <div className="space-y-3">
          <Button
            size="sm"
            variant="outline"
            className="w-full border-gray-700 hover:bg-gray-800"
            onClick={() => {
              setEditingCustomSkill(null);
              setIsModalOpen(true);
            }}
          >
            <Plus className="w-4 h-4 mr-1.5" /> Create New Skill
          </Button>

          {filteredCustomSkills.map((skill) => {
            const isOwner = skill.created_by === currentUserId;
            const isShared = skill.scope === 'org';

            return (
              <CustomSkillCard
                key={skill.id}
                skill={skill}
                isOwner={isOwner}
                isShared={isShared}
                isSharing={isSharing === skill.id}
                isUnsharing={isUnsharing === skill.id}
                teamSharingEnabled={teamSharingEnabled}
                onEdit={() => {
                  setEditingCustomSkill(skill);
                  setIsModalOpen(true);
                }}
                onDelete={() => handleDeleteCustomSkill(skill.id, skill.name)}
                onShare={() => handleShareCustomSkill(skill.id)}
                onUnshare={() => handleUnshareCustomSkill(skill.id)}
              />
            );
          })}

          {filteredCustomSkills.length === 0 && (
            <div className="text-center py-8 text-gray-500 text-sm">
              {searchQuery ? 'No custom skills match your search' : 'No custom skills yet'}
            </div>
          )}
        </div>
      )}

      {/* Whitelisted Domains */}
      {activeTab === 'whitelisted' && (
        <div className="space-y-3">
          {domainEntries.length > 0 ? (
            <>
              <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Globe className="w-4 h-4 text-gray-400" />
                  <span className="text-sm font-medium text-white">Enable all custom domains</span>
                </div>
                <Switch
                  checked={domainEntries.every(e => e.skill.domain_active)}
                  onCheckedChange={(checked) => {
                    for (const entry of domainEntries) {
                      if (entry.skill.domain_active !== checked) {
                        if (entry.source === 'byaan') {
                          const s = entry.skill as SkillStatus;
                          const hasPersonal = s.scopes_configured?.includes('user') && s.user_scope_created_by === currentUserId;
                          toggleSkillDomain(s.skill_name, checked, hasPersonal ? 'user' : 'org');
                        } else {
                          toggleCustomSkillDomain((entry.skill as CustomSkill).id, checked);
                        }
                      }
                    }
                  }}
                />
              </div>
            {domainEntries.map((entry) => {
              if (entry.source === 'byaan') {
                const skill = entry.skill as SkillStatus;
                const hasPersonal = skill.scopes_configured?.includes('user') && skill.user_scope_created_by === currentUserId;
                const scope: SkillScope = hasPersonal ? 'user' : 'org';
                return (
                  <div
                    key={`byaan-${skill.skill_name}`}
                    className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 flex items-center justify-between gap-3"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="text-2xl flex-shrink-0">{skill.emoji || '🔌'}</span>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-white text-sm">{skill.display_name}</span>
                          {skill.domain_active ? (
                            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-400">
                              <Shield className="w-2.5 h-2.5" /> Active
                            </span>
                          ) : (
                            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/10 text-red-400">
                              Disabled
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-gray-400 mt-0.5 font-mono">
                          {skill.domain}
                        </p>
                      </div>
                    </div>
                    <Switch
                      checked={skill.domain_active}
                      onCheckedChange={(checked) => toggleSkillDomain(skill.skill_name, checked, scope)}
                    />
                  </div>
                );
              } else {
                const skill = entry.skill as CustomSkill;
                return (
                  <div
                    key={`custom-${skill.id}`}
                    className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 flex items-center justify-between gap-3"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="text-2xl flex-shrink-0">📝</span>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-white text-sm">{skill.name}</span>
                          <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20">
                            Custom
                          </span>
                          {skill.domain_active ? (
                            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-400">
                              <Shield className="w-2.5 h-2.5" /> Active
                            </span>
                          ) : (
                            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/10 text-red-400">
                              Disabled
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-gray-400 mt-0.5 font-mono">
                          {skill.api_domain}
                        </p>
                      </div>
                    </div>
                    <Switch
                      checked={skill.domain_active}
                      onCheckedChange={(checked) => toggleCustomSkillDomain(skill.id, checked)}
                    />
                  </div>
                );
              }
            })}
            </>
          ) : (
            <div className="text-center py-8 text-gray-500 text-sm">
              No custom domains configured
            </div>
          )}
        </div>
      )}

      {/* Write Skill Modal */}
      <WriteSkillModal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          setEditingCustomSkill(null);
        }}
        onSave={handleSaveCustomSkill}
        editingSkill={editingCustomSkill}
      />

      {/* Delete Confirmation Modal */}
      <ConfirmationModal
        isOpen={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        title="Delete Skill?"
        message={<>This will permanently delete <span className="font-semibold text-white">"{deleteTarget?.name}"</span>. This cannot be undone.</>}
        confirmText="Delete"
        type="danger"
        loading={isDeleting}
      />

    </div>
  );
}

function CustomSkillCard({
  skill,
  isOwner,
  isShared,
  isSharing,
  isUnsharing,
  teamSharingEnabled,
  onEdit,
  onDelete,
  onShare,
  onUnshare,
}: {
  skill: CustomSkill;
  isOwner: boolean;
  isShared: boolean;
  isSharing: boolean;
  isUnsharing: boolean;
  teamSharingEnabled: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onShare: () => void;
  onUnshare: () => void;
}) {
  const isGitHubSkill = skill.skill_type === 'github_analysis';

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-2xl flex-shrink-0">{isGitHubSkill ? <Github className="w-5 h-5 text-gray-300" /> : '📝'}</span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-white">{skill.name}</span>
              {isGitHubSkill && skill.github_repo_name && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-500/10 text-purple-400 border border-purple-500/20">
                  <Github className="w-2.5 h-2.5" /> {skill.github_repo_name}
                </span>
              )}
              {isOwner && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-brand-orange/10 text-orange-400 border border-brand-orange/20">
                  <Key className="w-2.5 h-2.5" /> Created by you
                </span>
              )}
              {isShared && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-400 border border-green-500/20">
                  <Users className="w-2.5 h-2.5" /> Team
                </span>
              )}
              {skill.can_execute_api ? (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-400 border border-green-500/20">
                  <Zap className="w-2.5 h-2.5" /> API Enabled
                </span>
              ) : (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-yellow-500/10 text-yellow-400 border border-yellow-500/20">
                  Informational only
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{skill.description}</p>
            {!isOwner && skill.created_by_name && (
              <p className="text-xs text-gray-400 mt-0.5">Created by {skill.created_by_name}</p>
            )}
          </div>
        </div>
      </div>

      {/* Actions for owner */}
      {isOwner && (
        <div className="mt-4 space-y-3">
          {/* Team sharing toggle */}
          {teamSharingEnabled && (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Users className="w-4 h-4 text-gray-400" />
                <span className="text-sm text-gray-300">Share with team</span>
              </div>
              <div className="flex items-center gap-2">
                {(isSharing || isUnsharing) && (
                  <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />
                )}
                <Switch
                  checked={isShared}
                  onCheckedChange={(checked) => checked ? onShare() : onUnshare()}
                  disabled={isSharing || isUnsharing}
                />
              </div>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={onEdit}
              className="text-xs border-gray-700 hover:bg-gray-800"
            >
              <Edit2 className="w-3 h-3 mr-1.5" /> Edit
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={onDelete}
              className="text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10"
            >
              <Trash2 className="w-3 h-3 mr-1.5" /> Delete
            </Button>
          </div>
        </div>
      )}

      {/* Edit button for non-owners */}
      {!isOwner && (
        <div className="mt-3">
          <Button
            size="sm"
            variant="outline"
            onClick={onEdit}
            className="text-xs border-gray-700 hover:bg-gray-800"
          >
            <Edit2 className="w-3 h-3 mr-1.5" /> View Instructions
          </Button>
        </div>
      )}
    </div>
  );
}

function SkillCard({
  skill,
  hasPersonal,
  hasOrg,
  isOrgOwner,
  isEditing,
  credentials,
  isSaving,
  isSharing,
  isUnsharing,
  teamSharingEnabled,
  onEdit,
  onCredentialChange,
  onSave,
  onCancel,
  onDisconnect,
  onShare,
  onUnshare,
}: {
  skill: SkillStatus;
  hasPersonal: boolean | undefined;
  hasOrg: boolean | undefined;
  isOrgOwner: boolean;
  isEditing: boolean;
  credentials: Record<string, string>;
  isSaving: boolean;
  isSharing: boolean;
  isUnsharing: boolean;
  teamSharingEnabled: boolean;
  onEdit: () => void;
  onCredentialChange: (key: string, value: string) => void;
  onSave: () => void;
  onCancel: () => void;
  onDisconnect: () => void;
  onShare: () => void;
  onUnshare: () => void;
}) {
  const isConfigured = hasPersonal || hasOrg;
  const visibleFields = skill.credential_fields.filter(f =>
    isFieldVisible(f, skill.credential_fields, credentials)
  );
  const allFieldsFilled = isConfigured
    ? Object.values(credentials).some(v => v?.trim())
    : visibleFields
        .filter(f => !f.optional)
        .every(f => Boolean(credentials[f.key]?.trim() || (f.type === 'select' && f.default)));

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-2xl flex-shrink-0">{skill.emoji || '🔌'}</span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-white">{skill.display_name}</span>
              {hasPersonal && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-brand-orange/10 text-orange-400 border border-brand-orange/20">
                  <Key className="w-2.5 h-2.5" /> Personal
                </span>
              )}
              {hasOrg && !hasPersonal && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-400 border border-green-500/20">
                  <Users className="w-2.5 h-2.5" /> Team
                </span>
              )}
              {hasOrg && hasPersonal && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-400 border border-green-500/20">
                  <Users className="w-2.5 h-2.5" /> Shared
                </span>
              )}
              {isConfigured && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-400">
                  <CheckCircle className="w-2.5 h-2.5" /> Active
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500 mt-0.5 truncate">{skill.description}</p>
            {hasOrg && !hasPersonal && skill.org_scope_created_by_name && (
              <p className="text-xs text-gray-400 mt-0.5">Shared by {skill.org_scope_created_by_name}</p>
            )}
          </div>
        </div>
        {skill.homepage && (
          <a
            href={skill.homepage}
            target="_blank"
            rel="noopener noreferrer"
            className="p-1.5 rounded-lg hover:bg-gray-800 transition-colors flex-shrink-0"
            title="View documentation"
          >
            <ExternalLink className="w-3.5 h-3.5 text-gray-500" />
          </a>
        )}
      </div>

      {/* Edit form */}
      {isEditing && (
        <div className="mt-4 space-y-4">
          {visibleFields.map((field, index) => (
            <div key={field.key} className="space-y-1">
              <label className="text-xs font-medium text-gray-300">{field.label}</label>
              {field.type === 'select' ? (
                <select
                  value={credentials[field.key] || field.default || ''}
                  onChange={(e) => onCredentialChange(field.key, e.target.value)}
                  className="w-full h-9 px-3 rounded-md bg-[#0d0d0d] border border-gray-700 text-sm text-white"
                >
                  {(field.options || []).map(opt => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              ) : (
                <Input
                  type={field.optional ? 'text' : 'password'}
                  placeholder={isConfigured && !field.optional
                    ? 'Leave blank to keep current value'
                    : field.placeholder || `Enter ${field.label.toLowerCase()}...`}
                  value={credentials[field.key] || ''}
                  onChange={(e) => onCredentialChange(field.key, e.target.value)}
                  className="bg-[#0d0d0d] border-gray-700 text-sm"
                  autoFocus={index === 0}
                />
              )}
              {field.help && <p className="text-xs text-gray-500">{field.help}</p>}
            </div>
          ))}
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={onSave}
              disabled={!allFieldsFilled || isSaving}
              className="bg-brand-orange hover:bg-brand-orange/90"
            >
              {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Save'}
            </Button>
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Configured skill actions */}
      {hasPersonal && !isEditing && (
        <div className="mt-4 space-y-3">
          {/* Team sharing toggle */}
          {teamSharingEnabled && (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Users className="w-4 h-4 text-gray-400" />
                <span className="text-sm text-gray-300">Share with team</span>
              </div>
              <div className="flex items-center gap-2">
                {(isSharing || isUnsharing) && (
                  <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />
                )}
                <Switch
                  checked={!!(hasOrg && isOrgOwner)}
                  onCheckedChange={(checked) => checked ? onShare() : onUnshare()}
                  disabled={isSharing || isUnsharing || (hasOrg && !isOrgOwner)}
                />
              </div>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              size="sm"
              variant="outline"
              onClick={onEdit}
              className="text-xs border-gray-700 hover:bg-gray-800"
            >
              <Key className="w-3 h-3 mr-1.5" /> Update Configuration
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={onDisconnect}
              className="text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10"
            >
              Disconnect
            </Button>
          </div>
        </div>
      )}

      {/* Available skill - add key button */}
      {!hasPersonal && !isEditing && (
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <Button
            size="sm"
            variant="outline"
            onClick={onEdit}
            className="text-xs border-gray-700 hover:bg-gray-800"
          >
            <Key className="w-3 h-3 mr-1.5" /> Add Configuration
          </Button>
        </div>
      )}

    </div>
  );
}

