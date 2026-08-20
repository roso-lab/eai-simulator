/*********************************************************************
* Software License Agreement (BSD License)
*
*  Copyright (c) 2010, Rice University
*  All rights reserved.
*
*  Redistribution and use in source and binary forms, with or without
*  modification, are permitted provided that the following conditions
*  are met:
*
*   * Redistributions of source code must retain the above copyright
*     notice, this list of conditions and the following disclaimer.
*   * Redistributions in binary form must reproduce the above
*     copyright notice, this list of conditions and the following
*     disclaimer in the documentation and/or other materials provided
*     with the distribution.
*   * Neither the name of the Rice University nor the names of its
*     contributors may be used to endorse or promote products derived
*     from this software without specific prior written permission.
*
*  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
*  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
*  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
*  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
*  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
*  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
*  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
*  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
*  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
*  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
*  POSSIBILITY OF SUCH DAMAGE.
*********************************************************************/

/* Author: Justin Kottinger */

#ifndef OMPL_MULTIROBOT_CONTROL_SPACE_INFORMATION_
#define OMPL_MULTIROBOT_CONTROL_SPACE_INFORMATION_

#include "ompl/multirobot/base/SpaceInformation.h"
#include "ompl/control/SpaceInformation.h"
#include "ompl/multirobot/control/SystemMerger.h"

namespace ompl
{
    namespace multirobot
    {
        namespace control
        {
            /// @cond IGNORE
            /** \brief Forward declaration of ompl::base::SpaceInformation */
            OMPL_CLASS_FORWARD(SpaceInformation);
            /// @endcond

            class SpaceInformation : public ompl::multirobot::base::SpaceInformation
            {
            public:
                // non-copyable
                SpaceInformation(const SpaceInformation &) = delete;
                SpaceInformation &operator=(const SpaceInformation &) = delete;

                /** \brief Constructor. Sets the instance of the multi-agent space information */
                SpaceInformation();

                /** \brief Construct a multi-agent space information from a list of indivudal space informations */
                SpaceInformation(const std::vector<ompl::control::SpaceInformation> &individuals);

                ~SpaceInformation() override
                {
                    for (auto &siC: individuals_)
                        siC.reset();
                    if (systemMerger_)
                        systemMerger_.reset();
                }

                /** \brief Adds an individual as part of the multi-agent state space. */
                void addIndividual(const ompl::control::SpaceInformationPtr &individual);

                /** \brief Set the instance of the plan validity checker to use. */
                void setSystemMerger(const SystemMergerPtr &merger)
                {
                    systemMerger_ = merger;
                }

                std::pair<const SpaceInformationPtr, const ompl::multirobot::base::ProblemDefinitionPtr> merge(const int index1, const int index2) const
                {
                    return systemMerger_->merge(index1, index2);
                }

                /** \brief Adds a dynamic obstacle for `individual1` where `individual2` is located at `state` at some `time'. */
                void addDynamicObstacleForIndividual(const unsigned int individual1, const unsigned int individual2, ompl::base::State* state, const double time) const override
                {
                    individuals_[individual1]->addDynamicObstacle(time, getIndividual(individual2), state);
                }

                ompl::base::PlannerPtr allocatePlannerForIndividual(const unsigned int index) const override
                {
                    return pa_(individuals_[index]);
                }

                /** \brief Get a specific subspace from the compound state space */
                const ompl::control::SpaceInformationPtr &getIndividual(unsigned int index) const;

                const SystemMergerPtr &getSystemMerger() const {return systemMerger_;};

                virtual void setup() override;

            protected:
                /** \brief The individual space informations that make up the multi-agent state space */
                std::vector<ompl::control::SpaceInformationPtr> individuals_;

                /** \brief An instance of the plan validity checker */
                SystemMergerPtr systemMerger_{nullptr};
            };
        }
    }
}

#endif
