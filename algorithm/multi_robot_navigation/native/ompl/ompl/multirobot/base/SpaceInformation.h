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

#ifndef OMPL_MULTIROBOT_SPACE_INFORMATION_
#define OMPL_MULTIROBOT_SPACE_INFORMATION_

#include "ompl/base/SpaceInformation.h"
#include "ompl/base/Planner.h"

namespace ompl
{
    namespace multirobot
    {
        namespace base
        {
            /// @cond IGNORE
            /** \brief Forward declaration of ompl::base::SpaceInformation */
            OMPL_CLASS_FORWARD(SpaceInformation);
            /// @endcond

            class SpaceInformation
            {
            public:
                // non-copyable
                SpaceInformation(const SpaceInformation &) = delete;
                SpaceInformation &operator=(const SpaceInformation &) = delete;

                /** \brief Constructor. Sets the instance of the multi-agent space information */
                SpaceInformation();

                /** \brief Construct a multi-agent space information from a list of indivudal space informations */
                SpaceInformation(const std::vector<ompl::base::SpaceInformation> &individuals);

                virtual ~SpaceInformation()
                {
                    for (auto &si: individuals_)
                        si.reset();
                }

                /** \brief Cast this instance to a desired type. */
                template <class T>
                const T *as() const
                {
                    /** \brief Make sure the type we are allocating is indeed a Plan */
                    BOOST_CONCEPT_ASSERT((boost::Convertible<T *, SpaceInformation *>));

                    return static_cast<const T *>(this);
                }

                /** \brief Cast this instance to a desired type. */
                template <class T>
                T *as()
                {
                    /** \brief Make sure the type we are allocating is indeed a Plan */
                    BOOST_CONCEPT_ASSERT((boost::Convertible<T *, SpaceInformation *>));

                    return static_cast<T *>(this);
                }

                /** \brief Adds an individual as part of the multi-agent state space. */
                void addIndividual(const ompl::base::SpaceInformationPtr &individual);

                /** \brief Adds a dynamic obstacle for `individual1` where `individual2` is located at `state` at some `time'. */
                virtual void addDynamicObstacleForIndividual(const unsigned int individual1, const unsigned int individual2, ompl::base::State* state, const double time) const
                {
                    individuals_[individual1]->addDynamicObstacle(time, getIndividual(individual2), state);
                }

                virtual ompl::base::PlannerPtr allocatePlannerForIndividual(const unsigned int index) const
                {
                    return pa_(individuals_[index]);
                }

                void setPlannerAllocator(const ompl::base::PlannerAllocator &pa)
                {
                    pa_ = pa;
                }

                bool hasPlannerAllocator() const
                {
                    return (pa_) ? true : false;
                }

                /** \brief Get a specific subspace from the compound state space */
                const ompl::base::SpaceInformationPtr &getIndividual(unsigned int index) const;

                /** \brief Get the number of individuals that make up the multi-agent state space */
                unsigned int getIndividualCount() const;

                /** \brief Assures that all of the individual state spaces are setup */
                virtual void setup();

                /** \brief Returns true if setup() has been called */
                bool isSetup()
                {
                    return setup_;
                }

                void lock()
                {
                    locked_ = true;
                }

            protected:
                /** \brief The individual space informations that make up the multi-agent state space */
                std::vector<ompl::base::SpaceInformationPtr> individuals_;

                /** \brief The planner allocator for the system -- responsible for providing single-agent PlannerPtr's to the multi-agent planners */
                ompl::base::PlannerAllocator pa_;

                /** \brief The number of indivudals in the multi-agent state space */
                unsigned int individualCount_{0u};

                /** \brief Boolean that indicates that there are no additional individuals to add */
                bool locked_;

                /** \brief Flag that notifies the user if all individual's are set-up for planning */
                bool setup_;
            };
        }
    }
}

#endif
