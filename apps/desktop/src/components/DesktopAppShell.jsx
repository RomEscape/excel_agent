import React from 'react';

export default function DesktopAppShell() {
  return (
    <div className="w-[7570px] h-[1052px] relative bg-lime-200 overflow-hidden">
        {/* Screen 1: Welcome / Status */}
        <div className="w-[1400px] h-[900px] left-[85px] top-[76px] absolute bg-white rounded-2xl outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start overflow-hidden">
            <div className="self-stretch px-5 py-2.5 bg-white outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-between items-center overflow-hidden">
                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">● ● ●   김대리 AI - 로컬 워크스페이스</div>
                <div className="flex justify-start items-center gap-3">
                    <div className="size-3 bg-zinc-300 rounded-full" />
                    <div className="size-3 bg-zinc-300 rounded-full" />
                    <div className="size-3 bg-zinc-300 rounded-full" />
                    <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">보안 상태</div>
                </div>
            </div>
            <div className="self-stretch flex-1 py-14 bg-white flex flex-col justify-center items-center gap-10 overflow-hidden">
                <div className="bg-white flex flex-col justify-start items-center gap-2 overflow-hidden">
                    <div className="text-center justify-start text-zinc-900 text-3xl font-bold font-['Noto_Sans_KR']">좋은 아침입니다. 무엇을 도와드릴까요?</div>
                    <div className="text-center justify-start text-lime-600 text-base font-bold font-['Noto_Sans_KR']">당신의 전문 비서 김대리가 대기중입니다.</div>
                </div>
                <div className="bg-white inline-flex justify-start items-start gap-5 overflow-hidden">
                    <div className="pl-5 pr-6 py-5 bg-slate-50 rounded-[20px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex justify-start items-center gap-3.5 overflow-hidden">
                        <div className="size-12 bg-stone-50 rounded-xl" />
                        <div className="bg-white inline-flex flex-col justify-start items-start gap-[3px] overflow-hidden">
                            <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">보안 상태</div>
                            <div className="justify-start text-zinc-900 text-sm font-bold font-['Noto_Sans_KR']">100% 로컬 / 오프라인 준비됨</div>
                        </div>
                    </div>
                    <div className="pl-5 pr-6 py-5 bg-slate-50 rounded-[20px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex justify-start items-center gap-3.5 overflow-hidden">
                        <div className="size-12 bg-stone-50 rounded-xl" />
                        <div className="bg-white inline-flex flex-col justify-start items-start gap-[3px] overflow-hidden">
                            <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">최근 분석 데이터</div>
                            <div className="justify-start text-zinc-900 text-sm font-bold font-['Noto_Sans_KR']">8월_매출표.xlsx (어제)</div>
                        </div>
                    </div>
                    <div className="pl-5 pr-6 py-5 bg-slate-50 rounded-[20px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex justify-start items-center gap-3.5 overflow-hidden">
                        <div className="size-12 bg-stone-50 rounded-xl" />
                        <div className="bg-white inline-flex flex-col justify-start items-start gap-[3px] overflow-hidden">
                            <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">예정된 자동화</div>
                            <div className="justify-start text-zinc-900 text-sm font-bold font-['Noto_Sans_KR']">오후 2시 주간 리포트 생성</div>
                        </div>
                    </div>
                </div>
                <div className="px-10 py-4 bg-lime-600 rounded-[40px] inline-flex justify-center items-start overflow-hidden">
                    <div className="justify-start text-white text-base font-bold font-['Noto_Sans_KR']">새 업무 시작하기</div>
                </div>
            </div>
        </div>

        {/* Screen 2: Chat Interface */}
        <div className="w-[1400px] h-[900px] left-[1585px] top-[76px] absolute bg-white rounded-2xl outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start overflow-hidden">
            <div className="self-stretch py-2.5 bg-white outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-center items-start overflow-hidden">
                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">● ● ●   김대리 AI - 로컬 워크스페이스</div>
            </div>
            <div className="self-stretch flex-1 bg-white inline-flex justify-start items-start overflow-hidden">
                <div className="w-72 self-stretch p-5 bg-gray-50 outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start gap-5 overflow-hidden">
                    <div className="bg-white inline-flex justify-start items-center gap-2.5 overflow-hidden">
                        <div className="size-9 bg-lime-600 rounded-full" />
                        <div className="bg-white inline-flex flex-col justify-start items-start gap-0.5 overflow-hidden">
                            <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">로컬 사용자</div>
                            <div className="justify-start text-lime-800 text-xs font-normal font-['Noto_Sans_KR']">● Local Protected</div>
                        </div>
                    </div>
                    <div className="self-stretch py-2.5 bg-white rounded-lg outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-center items-start overflow-hidden">
                        <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">+ 새 대화</div>
                    </div>
                    <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">오늘</div>
                    <div className="self-stretch p-2.5 bg-lime-100 rounded-lg inline-flex justify-start items-start overflow-hidden">
                        <div className="w-56 justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">8월 매출 데이터 분석</div>
                    </div>
                    <div className="self-stretch p-2.5 bg-white rounded-lg inline-flex justify-start items-start overflow-hidden">
                        <div className="w-56 justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">주간 업무 보고서 작성</div>
                    </div>
                    <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">어제</div>
                    <div className="self-stretch p-2.5 bg-white inline-flex justify-start items-start overflow-hidden">
                        <div className="w-56 justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">신규 거래처 계약서 검토</div>
                    </div>
                    <div className="self-stretch p-2.5 bg-white inline-flex justify-start items-start overflow-hidden">
                        <div className="w-56 justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">마케팅 카피라이팅 아이디어</div>
                    </div>
                </div>
                <div className="flex-1 self-stretch bg-white inline-flex flex-col justify-start items-start overflow-hidden">
                    <div className="self-stretch flex-1 p-8 bg-white flex flex-col justify-start items-start gap-5 overflow-hidden">
                        <div className="bg-white inline-flex justify-start items-start gap-3 overflow-hidden">
                            <div className="size-9 bg-lime-600 rounded-full" />
                            <div className="bg-white inline-flex flex-col justify-start items-start gap-1.5 overflow-hidden">
                                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">김대리 AI  [로컬]</div>
                                <div className="w-[532px] h-14 relative bg-neutral-100 rounded-xl overflow-hidden">
                                    <div className="w-[500px] left-[16px] top-[14px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">안녕하세요! 당신의 로컬 컴퓨터에서만 실행되는 든든한 비서, 김대리입니다.<br/>어떤 업무를 도와드릴까요? 파일을 던져주시거나 질문을 입력해주세요.</div>
                                </div>
                            </div>
                        </div>
                        <div className="self-stretch bg-white inline-flex justify-end items-start overflow-hidden">
                            <div className="w-[452px] h-14 relative bg-lime-600 rounded-xl overflow-hidden">
                                <div className="w-96 left-[16px] top-[14px] absolute justify-start text-white text-xs font-normal font-['Noto_Sans_KR']">["8월_매출데이터_원본.xlsx" 첨부됨]<br/>이 엑셀에서 top10 제품만 필터링해서 심층 데이터 분석해줘</div>
                            </div>
                        </div>
                        <div className="bg-white inline-flex justify-start items-start gap-3 overflow-hidden">
                            <div className="size-9 bg-lime-600 rounded-full" />
                            <div className="bg-white inline-flex flex-col justify-start items-start gap-1.5 overflow-hidden">
                                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">김대리 AI  [로컬]</div>
                                <div className="w-[532px] h-14 relative bg-neutral-100 rounded-xl overflow-hidden">
                                    <div className="w-[500px] left-[16px] top-[14px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">네, 로컬 환경에서 엑셀 데이터를 안전하게 분석했습니다. 가장 매출이 높았던 상위 3개 제품군을 필터링한 결과입니다. (실시간 셀 채우기 데모)</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div className="self-stretch px-8 pt-4 pb-5 bg-white flex flex-col justify-start items-start gap-2 overflow-hidden">
                        <div className="self-stretch px-4 py-3 bg-white rounded-2xl outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-start items-center gap-2.5 overflow-hidden">
                            <div className="justify-start text-stone-500 text-base font-normal font-['Noto_Sans_KR']">📎</div>
                            <div className="w-[900px] justify-start text-stone-500 text-xs font-normal font-['Noto_Sans_KR']">김대리에게 업무를 지시하세요...</div>
                            <div className="size-9 bg-lime-600 rounded-full" />
                        </div>
                        <div className="text-center justify-start text-stone-500 text-xs font-normal font-['Noto_Sans_KR']">🛡 모든 데이터는 당신의 컴퓨터 안에서만 안전하게 처리됩니다.</div>
                    </div>
                </div>
            </div>
        </div>

        {/* Screen 3: Chat with Quick Prompts */}
        <div className="w-[1400px] h-[900px] left-[3085px] top-[76px] absolute bg-white rounded-2xl outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start overflow-hidden">
            <div className="self-stretch py-2.5 bg-white outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-center items-start overflow-hidden">
                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">● ● ●   김대리 AI - 로컬 워크스페이스</div>
            </div>
            <div className="self-stretch flex-1 bg-white inline-flex justify-start items-start overflow-hidden">
                <div className="w-72 self-stretch p-5 bg-gray-50 outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start gap-5 overflow-hidden">
                    <div className="bg-white inline-flex justify-start items-center gap-2.5 overflow-hidden">
                        <div className="size-9 bg-lime-600 rounded-full" />
                        <div className="bg-white inline-flex flex-col justify-start items-start gap-0.5 overflow-hidden">
                            <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">로컬 사용자</div>
                            <div className="justify-start text-lime-800 text-xs font-normal font-['Noto_Sans_KR']">● Local Protected</div>
                        </div>
                    </div>
                    <div className="self-stretch py-2.5 bg-white rounded-lg outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-center items-start overflow-hidden">
                        <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">+ 새 대화</div>
                    </div>
                    <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">오늘</div>
                    <div className="self-stretch p-2.5 bg-lime-100 rounded-lg inline-flex justify-start items-start overflow-hidden">
                        <div className="w-56 justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">8월 매출 데이터 분석</div>
                    </div>
                </div>
                <div className="flex-1 self-stretch bg-white inline-flex flex-col justify-start items-start overflow-hidden">
                    <div className="self-stretch flex-1 p-8 bg-white flex flex-col justify-start items-start gap-5 overflow-hidden">
                        <div className="bg-white inline-flex justify-start items-start gap-3 overflow-hidden">
                            <div className="size-9 bg-lime-600 rounded-full" />
                            <div className="bg-white inline-flex flex-col justify-start items-start gap-1.5 overflow-hidden">
                                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">김대리 AI  [로컬]</div>
                                <div className="w-[532px] h-14 relative bg-neutral-100 rounded-xl overflow-hidden">
                                    <div className="w-[500px] left-[16px] top-[14px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">안녕하세요! 당신의 로컬 컴퓨터에서만 실행되는 든든한 비서, 김대리입니다.<br/>어떤 업무를 도와드릴까요? 파일을 던져주시거나 질문을 입력해주세요.</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div className="self-stretch px-8 pt-4 pb-5 bg-white flex flex-col justify-start items-start gap-3 overflow-hidden">
                        <div className="self-stretch p-5 bg-white rounded-xl outline outline-1 outline-offset-[-1px] outline-gray-200 flex flex-col justify-start items-start gap-3.5 overflow-hidden">
                            <div className="px-3.5 py-2 bg-neutral-100 rounded-lg inline-flex justify-start items-center gap-2 overflow-hidden">
                                <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">📄 8월_매출데이터_원본.xlsx</div>
                                <div className="justify-start text-stone-500 text-xs font-normal font-['Noto_Sans_KR']">✕</div>
                            </div>
                            <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">✨ 엑셀 맞춤형 빠른 프롬프트 추천</div>
                            <div className="self-stretch bg-white inline-flex justify-start items-start gap-2 overflow-hidden">
                                <div className="px-3.5 py-2 bg-white rounded-[20px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex justify-start items-start overflow-hidden">
                                    <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">📊 기초 통계 분석</div>
                                </div>
                                <div className="px-3.5 py-2 bg-white rounded-[20px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex justify-start items-start overflow-hidden">
                                    <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">🔥 Top 10 필터링 &amp; 요약</div>
                                </div>
                                <div className="px-3.5 py-2 bg-white rounded-[20px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex justify-start items-start overflow-hidden">
                                    <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">🛡️ 개인정보 마스킹</div>
                                </div>
                                <div className="px-3.5 py-2 bg-white rounded-[20px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex justify-start items-start overflow-hidden">
                                    <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">🧹 중복 데이터 처리</div>
                                </div>
                                <div className="px-3.5 py-2 bg-white rounded-[20px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex justify-start items-start overflow-hidden">
                                    <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">💡 임원 보고용 대본 초안</div>
                                </div>
                            </div>
                        </div>
                        <div className="self-stretch px-4 py-3 bg-white rounded-2xl outline outline-1 outline-offset-[-1px] outline-lime-600 inline-flex justify-start items-center gap-2.5 overflow-hidden">
                            <div className="justify-start text-stone-500 text-base font-normal font-['Noto_Sans_KR']">📎</div>
                            <div className="w-[900px] justify-start text-stone-500 text-xs font-normal font-['Noto_Sans_KR']">김대리에게 업무를 지시하세요...</div>
                            <div className="size-9 bg-lime-600 rounded-full" />
                        </div>
                    </div>
                </div>
            </div>
        </div>

        {/* Screen 4: Chat with Excel Demo */}
        <div className="w-[1400px] h-[900px] left-[4585px] top-[76px] absolute bg-white rounded-2xl outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start overflow-hidden">
            <div className="self-stretch py-2.5 bg-white outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-center items-start overflow-hidden">
                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">● ● ●   김대리 AI - 로컬 워크스페이스</div>
            </div>
            <div className="self-stretch flex-1 bg-white inline-flex justify-start items-start overflow-hidden">
                <div className="w-72 self-stretch p-5 bg-gray-50 outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start gap-5 overflow-hidden">
                    <div className="bg-white inline-flex justify-start items-center gap-2.5 overflow-hidden">
                        <div className="size-9 bg-lime-600 rounded-full" />
                        <div className="bg-white inline-flex flex-col justify-start items-start gap-0.5 overflow-hidden">
                            <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">로컬 사용자</div>
                            <div className="justify-start text-lime-800 text-xs font-normal font-['Noto_Sans_KR']">● Local Protected</div>
                        </div>
                    </div>
                    <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">오늘</div>
                    <div className="self-stretch p-2.5 bg-lime-100 rounded-lg inline-flex justify-start items-start overflow-hidden">
                        <div className="w-56 justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">8월 매출 데이터 분석</div>
                    </div>
                </div>
                <div className="flex-1 self-stretch bg-white inline-flex flex-col justify-start items-start overflow-hidden">
                    <div className="self-stretch flex-1 p-8 bg-white flex flex-col justify-start items-start gap-5 overflow-hidden">
                        <div className="self-stretch bg-white inline-flex justify-end items-start overflow-hidden">
                            <div className="w-96 h-14 relative bg-lime-600 rounded-xl overflow-hidden">
                                <div className="w-96 left-[16px] top-[14px] absolute justify-start text-white text-xs font-normal font-['Noto_Sans_KR']">["8월_매출데이터_원본.xlsx" 첨부됨]<br/>결측치나 이상치가 있는지 확인해줘</div>
                            </div>
                        </div>
                        <div className="bg-white inline-flex justify-start items-start gap-3 overflow-hidden">
                            <div className="size-9 bg-lime-600 rounded-full" />
                            <div className="bg-white inline-flex flex-col justify-start items-start gap-2.5 overflow-hidden">
                                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">김대리 AI  [로컬]</div>
                                <div className="w-[700px] justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">네, 첨부하신 8월_매출데이터_원본.xlsx를 로컬에서 직접 열어 확인하고 있어요. 비어있는 셀을 채우는 중입니다.</div>
                                <div className="w-[700px] bg-white rounded-[10px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex flex-col justify-start items-start overflow-hidden">
                                    <div className="self-stretch px-4 py-3 bg-neutral-100 outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-between items-center overflow-hidden">
                                        <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">📄 8월_매출데이터_원본.xlsx</div>
                                        <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">AI 분석 중 ●●●</div>
                                    </div>
                                    <div className="self-stretch h-52 relative bg-white overflow-hidden">
                                        <div className="w-14 h-9 left-0 top-0 absolute bg-neutral-50 border-[0.50px] border-gray-200" />
                                        <div className="w-48 h-9 left-[56px] top-0 absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[91px] top-[10px] absolute justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">A</div>
                                        </div>
                                        <div className="w-36 h-9 left-[246px] top-0 absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[71px] top-[10px] absolute justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">B</div>
                                        </div>
                                        <div className="w-36 h-9 left-[396px] top-0 absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[71px] top-[10px] absolute justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">C</div>
                                        </div>
                                        <div className="w-36 h-9 left-[546px] top-0 absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[71px] top-[10px] absolute justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">D</div>
                                        </div>
                                        <div className="w-14 h-9 left-0 top-[36px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">1</div>
                                        </div>
                                        <div className="w-48 h-9 left-[56px] top-[36px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">제품군</div>
                                        </div>
                                        <div className="w-36 h-9 left-[246px] top-[36px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">매출(만원)</div>
                                        </div>
                                        <div className="w-36 h-9 left-[396px] top-[36px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">전월대비</div>
                                        </div>
                                        <div className="w-36 h-9 left-[546px] top-[36px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">비고</div>
                                        </div>
                                        <div className="w-14 h-9 left-0 top-[72px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">2</div>
                                        </div>
                                        <div className="w-48 h-9 left-[56px] top-[72px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">클라우드 솔루션</div>
                                        </div>
                                        <div className="w-36 h-9 left-[246px] top-[72px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">3,200</div>
                                        </div>
                                        <div className="w-36 h-9 left-[396px] top-[72px] absolute bg-green-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-lime-800 text-xs font-normal font-['Noto_Sans_KR']">+12%</div>
                                        </div>
                                        <div className="w-36 h-9 left-[546px] top-[72px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">-</div>
                                        </div>
                                        <div className="w-14 h-9 left-0 top-[108px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">3</div>
                                        </div>
                                        <div className="w-48 h-9 left-[56px] top-[108px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">보안 모듈 라이선스</div>
                                        </div>
                                        <div className="w-36 h-9 left-[246px] top-[108px] absolute bg-green-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-lime-800 text-xs font-normal font-['Noto_Sans_KR']">1,850</div>
                                        </div>
                                        <div className="w-36 h-9 left-[396px] top-[108px] absolute bg-green-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-lime-800 text-xs font-normal font-['Noto_Sans_KR']">+4%</div>
                                        </div>
                                        <div className="w-36 h-9 left-[546px] top-[108px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">-</div>
                                        </div>
                                        <div className="w-14 h-9 left-0 top-[144px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">4</div>
                                        </div>
                                        <div className="w-48 h-9 left-[56px] top-[144px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">데이터 백업 서비스</div>
                                        </div>
                                        <div className="w-36 h-9 left-[246px] top-[144px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">1,320</div>
                                        </div>
                                        <div className="w-36 h-9 left-[396px] top-[144px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">-3%</div>
                                        </div>
                                        <div className="w-36 h-9 left-[546px] top-[144px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">확인필요</div>
                                        </div>
                                        <div className="w-14 h-9 left-0 top-[180px] absolute bg-neutral-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">5</div>
                                        </div>
                                        <div className="w-48 h-9 left-[56px] top-[180px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">합계</div>
                                        </div>
                                        <div className="w-36 h-9 left-[246px] top-[180px] absolute bg-green-50 outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-lime-800 text-xs font-bold font-['Noto_Sans_KR']">6,370</div>
                                        </div>
                                        <div className="w-36 h-9 left-[396px] top-[180px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">-</div>
                                        </div>
                                        <div className="w-36 h-9 left-[546px] top-[180px] absolute bg-white outline outline-[0.50px] outline-offset-[-0.50px] outline-gray-200 overflow-hidden">
                                            <div className="left-[10px] top-[10px] absolute justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">-</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        {/* Screen 5: Chat with Dashboard */}
        <div className="w-[1400px] h-[900px] left-[6085px] top-[76px] absolute bg-white rounded-2xl outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start overflow-hidden">
            <div className="self-stretch py-2.5 bg-white outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex justify-center items-start overflow-hidden">
                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">● ● ●   김대리 AI - 로컬 워크스페이스</div>
            </div>
            <div className="self-stretch flex-1 bg-white inline-flex justify-start items-start overflow-hidden">
                <div className="w-72 self-stretch p-5 bg-gray-50 outline outline-1 outline-offset-[-1px] outline-gray-200 inline-flex flex-col justify-start items-start gap-5 overflow-hidden">
                    <div className="bg-white inline-flex justify-start items-center gap-2.5 overflow-hidden">
                        <div className="size-9 bg-lime-600 rounded-full" />
                        <div className="bg-white inline-flex flex-col justify-start items-start gap-0.5 overflow-hidden">
                            <div className="justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">로컬 사용자</div>
                            <div className="justify-start text-lime-800 text-xs font-normal font-['Noto_Sans_KR']">● Local Protected</div>
                        </div>
                    </div>
                    <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">오늘</div>
                    <div className="self-stretch p-2.5 bg-lime-100 rounded-lg inline-flex justify-start items-start overflow-hidden">
                        <div className="w-56 justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">8월 매출 데이터 분석</div>
                    </div>
                </div>
                <div className="flex-1 self-stretch bg-white inline-flex flex-col justify-start items-start overflow-hidden">
                    <div className="self-stretch flex-1 p-8 bg-white flex flex-col justify-start items-start gap-5 overflow-hidden">
                        <div className="self-stretch bg-white inline-flex justify-end items-start overflow-hidden">
                            <div className="w-96 h-14 relative bg-lime-600 rounded-xl overflow-hidden">
                                <div className="w-96 left-[16px] top-[14px] absolute justify-start text-white text-xs font-normal font-['Noto_Sans_KR']">["8월_매출데이터_원본.xlsx" 첨부됨]<br/>매출 기여도가 가장 높은 상위 10개 항목을 필터링해줘</div>
                            </div>
                        </div>
                        <div className="bg-white inline-flex justify-start items-start gap-3 overflow-hidden">
                            <div className="size-9 bg-lime-600 rounded-full" />
                            <div className="bg-white inline-flex flex-col justify-start items-start gap-2.5 overflow-hidden">
                                <div className="justify-start text-stone-500 text-xs font-bold font-['Noto_Sans_KR']">김대리 AI  [로컬]</div>
                                <div className="w-[600px] justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">네, 로컬 환경에서 엑셀 데이터를 안전하게 분석했습니다. 가장 매출이 높았던 상위 3개 제품군을 필터링한 결과입니다:</div>
                                <div className="w-[560px] p-4 bg-white rounded-[10px] outline outline-1 outline-offset-[-1px] outline-gray-200 flex flex-col justify-start items-start gap-2.5 overflow-hidden">
                                    <div className="justify-start text-lime-800 text-xs font-bold font-['Noto_Sans_KR']">8월 매출 Top 3 대시보드</div>
                                    <div className="self-stretch bg-white inline-flex justify-start items-center gap-2.5 overflow-hidden">
                                        <div className="w-28 justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">클라우드 솔루션</div>
                                        <div className="flex-1 h-3.5 relative bg-lime-600 rounded-md" />
                                    </div>
                                    <div className="self-stretch bg-white inline-flex justify-start items-center gap-2.5 overflow-hidden">
                                        <div className="w-28 justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">보안 모듈 라이선스</div>
                                        <div className="flex-1 h-3.5 relative bg-slate-400 rounded-md" />
                                    </div>
                                    <div className="self-stretch bg-white inline-flex justify-start items-center gap-2.5 overflow-hidden">
                                        <div className="w-28 justify-start text-zinc-900 text-xs font-bold font-['Noto_Sans_KR']">데이터 백업 서비스</div>
                                        <div className="flex-1 h-3.5 relative bg-slate-300 rounded-md" />
                                    </div>
                                </div>
                                <div className="w-[600px] justify-start text-zinc-900 text-xs font-normal font-['Noto_Sans_KR']">이 분석 결과를 바탕으로 팀원들에게 공유할 '8월 주간 매출 보고서' 초안을 작성해 드릴까요?</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
  );
}
